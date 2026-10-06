from magnus_tutor import courses as C
from magnus_tutor import prompts
from magnus_tutor.config import DEFAULT_SETTINGS, deep_merge, load_settings, save_settings
from magnus_tutor.context import system_prompt
from magnus_tutor.hardware import Hardware, recommend


def test_settings_merge_keeps_defaults(p):
    save_settings({"ollama": {"keep_alive": "1m"}}, p)
    s = load_settings(p)
    assert s["ollama"]["keep_alive"] == "1m"
    assert s["ollama"]["num_ctx"] == DEFAULT_SETTINGS["ollama"]["num_ctx"]
    assert deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 3}}) == {"a": {"b": 3, "c": 2}}


def test_course_create_load_archive(p):
    c = C.create_course("Electricity and Magnetism", short="E&M", term="Fall 2026", kind=["physics", "math"], p=p)
    assert c.slug == "em"
    for v in c.folders.values():
        assert (p.materials_root in __import__("pathlib").Path(v).parents)
    again = C.create_course("Electricity and Magnetism", short="E&M", p=p)
    assert again.slug == "em-2"
    loaded = C.load_course("em", p)
    assert loaded.title == "Electricity and Magnetism" and loaded.kind == ["physics", "math"]
    assert C.archive_term("Fall 2026", p) == ["em"]
    assert [x.slug for x in C.list_courses(p)] == ["em-2"]
    assert {x.slug for x in C.list_courses(p, include_archived=True)} == {"em", "em-2"}


def test_writing_course_detection(p):
    w = C.create_course("Magicians, Healers, and Holy Men", kind=["writing"], p=p)
    assert w.is_writing
    assert not C.create_course("QM", kind=["physics", "math"], p=p).is_writing


def test_profile_template_renders_as_empty(p):
    assert "hasn't filled in" in C.profile_text(C.load_profile(p))
    p.profile.write_text("name: Cooper\nschool: WashU\nlearning_preferences: [intuition first]\n")
    t = C.profile_text(C.load_profile(p))
    assert "Name: Cooper" in t and "intuition first" in t


def test_prompt_lookup_order_and_render(p):
    c = C.create_course("Electricity and Magnetism", short="E&M", p=p)
    text, where = prompts.source("office_hours", c, p)
    assert where == "global" and "{{course}}" in text
    (p.courses / c.slug / "prompts" / "office_hours.md").write_text("Course override for {{course}}.")
    text, where = prompts.source("office_hours", c, p)
    assert where == f"course:{c.slug}"
    assert prompts.render(text, {"course": "E&M"}) == "Course override for E&M."
    assert prompts.render("{{missing}} x", {}) == "x"


def test_prompt_save_history_restore(p):
    from magnus_tutor.db import get_db

    db = get_db(p)
    v1 = prompts.save(db, "office_hours", "Version one {{course}}", p=p)
    prompts.save(db, "office_hours", "Version two", p=p)
    hist = prompts.history(db, "office_hours")
    assert hist[0]["id"] > v1 and any(h["note"] == "before first edit in the app" for h in hist)
    prompts.restore(db, v1, p)
    assert (p.prompts / "office_hours.md").read_text() == "Version one {{course}}"
    prompts.reset(db, "office_hours", None, p)
    assert (p.prompts / "office_hours.md").read_text() == prompts.default_text("office_hours")


def test_system_prompt_includes_profile_course_and_mode(p):
    p.profile.write_text("name: Cooper\n")
    c = C.create_course("Electricity and Magnetism", short="E&M", instructor="Prof. X", p=p)
    sp = system_prompt("office_hours", c, p=p)
    assert "Name: Cooper" in sp and "Prof. X" in sp and "professor in office hours" in sp
    w = C.create_course("Magicians", kind=["writing"], p=p)
    assert "ghostwriter" in system_prompt("office_hours", w, p=p)
    assert "Sources from the student's materials" in system_prompt("ask", c, passages=[], p=p)


def test_recommendations_stay_under_half_ram():
    rec = recommend(Hardware(chip="Apple M5", ram_gb=24, apple_silicon=True, available_gb=10, on_battery=False))
    assert rec["preset"] == "standard" and rec["budget_gb"] == 12
    assert "qwen3.6:27b" not in rec["candidates"]
    assert recommend(Hardware("Apple M2", 16, True, 6, None))["preset"] == "light"
    assert recommend(Hardware("Apple M4 Max", 64, True, 40, None))["preset"] == "full"


def test_app_health_and_courses(p):
    from fastapi.testclient import TestClient

    from magnus_tutor.server.app import create_app

    C.create_course("Quantum", short="QM", p=p)
    with TestClient(create_app(manage_processes=False)) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert [c["slug"] for c in client.get("/api/courses").json()] == ["qm"]
        r = client.post("/api/courses", json={"title": "Cloud Computing", "short": "Cloud", "kind": ["code"], "languages": ["python"]})
        assert r.json()["slug"] == "cloud"
