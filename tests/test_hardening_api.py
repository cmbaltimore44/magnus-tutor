"""Cross-site GETs, mid-session textbook lookups, course field validation, untagged model names."""

import pytest
from fastapi.testclient import TestClient

from conftest import FakeProvider
from magnus_tutor import courses as C
from magnus_tutor import store
from magnus_tutor.config import load_settings
from magnus_tutor.db import get_db
from magnus_tutor.engine.tutor import Tutor
from magnus_tutor.ingest.exercises import parse_lookup


@pytest.fixture
def client(p):
    from magnus_tutor.server.app import create_app

    app = create_app(manage_processes=False)
    with TestClient(app) as c:
        st = app.state.tutor
        st.models.ollama = FakeProvider()

        async def _prepare(model):
            return None

        st.models._prepare = _prepare
        yield c


# --- item 4: Sec-Fetch-Site --------------------------------------------------------------


def test_cross_site_requests_refused_even_for_get(client):
    for site in ("cross-site", "same-site", "CROSS-SITE"):
        assert client.get("/api/library/search?q=gauss", headers={"sec-fetch-site": site}).status_code == 403
        assert client.get("/api/sessions", headers={"sec-fetch-site": site}).status_code == 403
        assert client.get("/api/documents/1/pages/0.png", headers={"sec-fetch-site": site}).status_code == 403
    # The app's own pages (same-origin), typed URLs (none) and non-browser clients (no header) work.
    assert client.get("/api/sessions", headers={"sec-fetch-site": "same-origin"}).status_code == 200
    assert client.get("/api/health", headers={"sec-fetch-site": "none"}).status_code == 200
    assert client.get("/api/sessions").status_code == 200
    r = client.patch("/api/settings", json={"alerts": "web"}, headers={"sec-fetch-site": "same-origin", "origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


# --- item 6: textbook lookups ----------------------------------------------------------------


def test_parse_lookup_variants():
    assert parse_lookup("new problem: 6.12") == ("6", "12")
    assert parse_lookup("next problem 6.12") == ("6", "12")
    assert parse_lookup("problem #6.12") == ("6", "12")
    assert parse_lookup("problem: 6.12") == ("6", "12")
    assert parse_lookup("Problem 6.12") == ("6", "12")
    assert parse_lookup("chapter 5 problem 3") == ("5", "3")
    assert parse_lookup("can we do exercise 2.7") == ("2", "7")
    assert parse_lookup("Problem 3: find E at r = 0.2 m") is None


class FakeIngest:
    def __init__(self, found=True):
        self.found = found
        self.calls = []

    async def lookup_exercise(self, course, chapter, number, read_page=True):
        self.calls.append((course, chapter, number))
        if not self.found:
            return None
        return {"text": f"Textbook exercise {chapter}.{number}: a sphere of radius 2 m carries 6 nC. Find E at its surface.",
                "label": f"Book ch. {chapter} #{number}, p. 40", "document_id": 1, "page_index": 3, "printed_page": "40"}

    def request_repair(self, *a):
        return False


async def _turn(t, sid, text):
    return [ev async for ev in t.turn(sid, text)]


PROBLEM = "A point charge q = 3 nC sits at the center of a sphere of radius 0.2 m. Find E at the surface."


@pytest.fixture
def tutor_setup(p, fake_models):
    mm, fp = fake_models
    course = C.create_course("Electricity and Magnetism", short="E&M", kind=["physics"], p=p)
    return mm, fp, course, load_settings(p), get_db(p)


async def test_mid_session_lookup_starts_new_problem(tutor_setup, p):
    mm, fp, course, settings, db = tutor_setup
    t = Tutor(p, db, mm, lambda: settings, ingest=FakeIngest())
    sid = t.create_session(course.slug)
    await _turn(t, sid, PROBLEM)
    prev = store.get_session(db, sid)["state"]["problem_id"]
    for msg in ["Problem 6.12", "chapter 5 problem 3", "can we do exercise 2.7"]:
        evs = await _turn(t, sid, msg)
        pid = store.get_session(db, sid)["state"]["problem_id"]
        assert pid != prev, msg
        pr = store.get_problem(db, pid)
        assert pr["text"].startswith("Textbook exercise") and pr["source"].startswith("Book ch.")
        meta = next(e for e in evs if e["type"] == "meta")
        assert meta["hint_level"] == 0 and meta["sources"][0]["label"].startswith("Book ch.")
        prev = pid


async def test_mid_session_lookup_not_found_keeps_problem(tutor_setup, p):
    mm, fp, course, settings, db = tutor_setup
    t = Tutor(p, db, mm, lambda: settings, ingest=FakeIngest(found=False))
    sid = t.create_session(course.slug)
    await _turn(t, sid, PROBLEM)
    first = store.get_session(db, sid)["state"]["problem_id"]
    await _turn(t, sid, "Problem 6.12")
    assert store.get_session(db, sid)["state"]["problem_id"] == first


async def test_new_problem_prefix_lookup_and_pasted_problem(tutor_setup, p):
    mm, fp, course, settings, db = tutor_setup
    t = Tutor(p, db, mm, lambda: settings, ingest=FakeIngest())
    sid = t.create_session(course.slug)
    await _turn(t, sid, "A block slides down a ramp. Find its speed.")
    await _turn(t, sid, "new problem: 6.12")
    pr = store.get_problem(db, store.get_session(db, sid)["state"]["problem_id"])
    assert pr["text"].startswith("Textbook exercise 6.12")
    await _turn(t, sid, "Problem 3: find E at r = 0.2 m from a 2 nC charge.")
    pr = store.get_problem(db, store.get_session(db, sid)["state"]["problem_id"])
    assert pr["text"].startswith("find E") and pr["source"] == "pasted"


async def test_attempts_mentioning_a_problem_number_are_not_hijacked(tutor_setup, p):
    mm, fp, course, settings, db = tutor_setup
    t = Tutor(p, db, mm, lambda: settings, ingest=FakeIngest())
    sid = t.create_session(course.slug)
    await _turn(t, sid, PROBLEM)
    first = store.get_session(db, sid)["state"]["problem_id"]
    await _turn(t, sid, "For problem 6.12 I got E = 13.5 N/C")
    assert store.get_session(db, sid)["state"]["problem_id"] == first


# --- item 10: course field validation ----------------------------------------------------------


def test_course_fields_are_validated(client):
    assert client.post("/api/courses", json={"title": 5}).status_code == 400
    assert client.post("/api/courses", json={"title": "QM", "topics": "not a list"}).status_code == 400
    assert client.post("/api/courses", json={"title": "QM", "kind": ["astrology"]}).status_code == 400
    assert client.post("/api/courses", json={"title": "QM", "description": "x" * 2001}).status_code == 400
    assert client.post("/api/courses", json={"title": "QM", "languages": ["python"] * 51}).status_code == 400
    r = client.post("/api/courses", json={"title": "QM", "short": "QM", "kind": ["physics"], "topics": ["spin"]})
    assert r.status_code == 200
    assert client.patch("/api/courses/qm", json={"status": "deleted"}).status_code == 400
    assert client.patch("/api/courses/qm", json={"instructor": {"x": 1}}).status_code == 400
    assert client.patch("/api/courses/qm", json={"topics": [1, 2]}).status_code == 400
    assert client.patch("/api/courses/qm", json={"title": ""}).status_code == 400
    assert client.patch("/api/courses/qm", json=["x"]).status_code == 400
    assert client.get("/api/courses/qm").json()["topics"] == ["spin"]
    assert client.patch("/api/courses/qm", json={"instructor": "Prof. Q", "status": "archived"}).status_code == 200
    assert client.post("/api/courses/wizard", json={"proposal": {"title": "Stat Mech", "kind": "physics"}}).status_code == 400
    assert client.post("/api/courses/wizard", json={"proposal": {"title": "Stat Mech", "topics": ["ensembles"]}}).status_code == 200


# --- item 11: untagged model names -------------------------------------------------------------------


async def test_untagged_model_names_match_latest(p):
    from magnus_tutor.llm.manager import ModelManager, model_key

    assert model_key("qwen3.5") == "qwen3.5:latest" and model_key("qwen3.5:9b") == "qwen3.5:9b"
    assert model_key("hf.co/user/model") == "hf.co/user/model:latest"
    assert model_key("localhost:11434/x") == "localhost:11434/x:latest"
    settings = load_settings(p)
    settings["models"] = {**settings["models"], "tutor": "qwen3.5", "embedding": "embed"}
    mm = ModelManager(settings)
    unloaded = []

    class Ollama:
        async def loaded(self):
            return [{"name": "qwen3.5:latest"}, {"name": "embed:latest"}, {"name": "other:7b"}]

        async def list_models(self):
            return [{"name": "qwen3.5:latest", "size": 6 * 1024**3}, {"name": "embed:latest", "size": 1}]

        async def unload(self, name):
            unloaded.append(name)

    mm.ollama = Ollama()
    await mm._prepare("qwen3.5")  # installed and loaded under its :latest name: no error
    assert unloaded == ["other:7b"], "the untagged tutor and embedding models are not unloaded as 'other'"
