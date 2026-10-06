"""HTTP API: prompt editor (save, history, restore, reset, preview, test) and the course wizard."""

import json

import pytest
from fastapi.testclient import TestClient

from conftest import FakeProvider


@pytest.fixture
def client(p):
    from magnus_tutor.server.app import create_app

    app = create_app(manage_processes=False)
    with TestClient(app) as c:
        st = app.state.tutor
        fp = FakeProvider()
        st.models.ollama = fp

        async def _prepare(model):
            return None

        st.models._prepare = _prepare
        c.fake = fp
        yield c


def test_prompt_editor_roundtrip(client, p):
    r = client.get("/api/prompts").json()
    assert {x["name"] for x in r["prompts"]} >= {"office_hours", "persona", "solver"} and "course" in r["variables"]
    original = client.get("/api/prompts/office_hours").json()["text"]
    client.put("/api/prompts/office_hours", json={"text": "Be extra brief in {{course}}."})
    assert (p.prompts / "office_hours.md").read_text() == "Be extra brief in {{course}}."
    hist = client.get("/api/prompts/office_hours").json()["history"]
    assert len(hist) == 2  # the original (baseline) and the new save
    baseline = [h for h in hist if h["note"] == "before first edit in the app"][0]
    client.post(f"/api/prompts/versions/{baseline['id']}/restore")
    assert (p.prompts / "office_hours.md").read_text() == original
    client.post("/api/prompts/office_hours/reset", json={})
    prev = client.post("/api/prompts/preview", json={"name": "office_hours", "text": "DRAFT for {{course}}"}).json()["text"]
    assert "DRAFT for general studies" in prev and "Hint level allowed this turn: 1 of 4" in prev
    assert "674" not in (p.prompts / "office_hours.md").read_text(), "preview never saves"


def test_course_override_and_removal(client, p):
    client.post("/api/courses", json={"title": "Electricity and Magnetism", "short": "E&M"})
    client.put("/api/prompts/office_hours", json={"text": "E&M only", "course": "em"})
    assert client.get("/api/prompts/office_hours?course=em").json()["where"] == "course:em"
    assert client.get("/api/prompts/office_hours").json()["where"] == "global"
    client.post("/api/prompts/office_hours/reset", json={"course": "em"})
    assert client.get("/api/prompts/office_hours?course=em").json()["where"] == "global"


def test_prompt_test_runs_scripted_conversations(client):
    client.fake.reply = lambda messages, **kw: "What have you tried so far?"
    lines = [json.loads(l[5:]) for l in client.post("/api/prompts/test", json={"name": "office_hours", "text": "Draft {{course}}", "scenarios": ["Stuck student"]}).text.splitlines() if l.startswith("data:")]
    kinds = [l["type"] for l in lines]
    assert kinds[0] == "scenario" and kinds.count("tutor") == 3 and kinds[-1] == "done"
    assert [l["hint_level"] for l in lines if l["type"] == "tutor"] == [0, 1, 2]
    assert any("Draft" in m["content"] for call in client.fake.calls for m in call["messages"] if m["role"] == "system")
    assert client.get("/api/sessions").json() == [], "test chats never land in your history"


def test_wizard_reads_syllabus_and_creates_course(client, p):
    proposal = {"title": "Quantum Mechanics II", "code": "PHYS 4220", "short": "QM2", "instructor": "Prof. Ruiz", "term": "Spring 2027",
                "description": "Second semester QM.", "kind": ["physics", "math"], "languages": [], "topics": ["perturbation theory", "scattering"],
                "schedule": "MWF 10am; midterm Mar 3", "grading": "HW 40%, exams 60%", "notation_conventions": "Dirac notation"}
    client.fake.reply = lambda messages, **kw: json.dumps(proposal)
    syllabus = b"PHYS 4220 Quantum Mechanics II, Spring 2027. Instructor: Prof. Ruiz. Topics: perturbation theory, scattering. Grading: HW 40%, exams 60%."
    r = client.post("/api/courses/from-syllabus", files={"file": ("syllabus.txt", syllabus, "text/plain")}).json()
    assert r["proposal"]["code"] == "PHYS 4220" and r["proposal"]["kind"] == ["physics", "math"]
    made = client.post("/api/courses/wizard", json={"proposal": r["proposal"], "syllabus_id": r["syllabus_id"]}).json()
    assert made["slug"] == "qm2"
    c = client.get("/api/courses/qm2").json()
    assert c["topics"] == ["perturbation theory", "scattering"] and c["grading"].startswith("HW")
    from pathlib import Path

    assert (Path(made["folders"]["other"]) / "syllabus.txt").exists()
    assert client.post("/api/courses/archive-term", json={"term": "Spring 2027"}).json() == {"archived": ["qm2"]}
    assert [x["slug"] for x in client.get("/api/courses").json()] == []
    assert [x["slug"] for x in client.get("/api/courses?archived=true").json()] == ["qm2"]
