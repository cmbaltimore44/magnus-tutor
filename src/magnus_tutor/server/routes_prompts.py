"""Prompt editor (list, edit, preview, history, restore, test) and the
new-course wizard (read a syllabus, propose course details)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from .. import courses as C
from .. import prompts
from ..context import system_prompt
from ..db import DB
from ..engine.tutor import Tutor

router = APIRouter(prefix="/api")


def st(request: Request):
    return request.app.state.tutor


def _course(request: Request, slug: str | None):
    return C.load_course(slug, st(request).p) if slug else None


@router.get("/prompts")
async def list_prompts(request: Request, course: str | None = None):
    c = _course(request, course)
    out = []
    for n in prompts.NAMES:
        text, where = prompts.source(n, c, st(request).p)
        out.append({"name": n, "description": prompts.DESCRIPTIONS[n], "where": where, "chars": len(text)})
    return {"prompts": out, "variables": prompts.VARIABLES}


@router.get("/prompts/{name}")
async def get_prompt(name: str, request: Request, course: str | None = None):
    if name not in prompts.NAMES:
        raise HTTPException(404, "no such prompt")
    c = _course(request, course)
    text, where = prompts.source(name, c, st(request).p)
    return {"name": name, "text": text, "where": where, "default": prompts.default_text(name), "used": prompts.used_variables(text),
            "history": prompts.history(st(request).db, name, course)}


@router.put("/prompts/{name}")
async def save_prompt(name: str, request: Request):
    body = await request.json()
    vid = prompts.save(st(request).db, name, body["text"], body.get("course"), body.get("note"), st(request).p)
    return {"ok": True, "version": vid}


@router.post("/prompts/{name}/reset")
async def reset_prompt(name: str, request: Request):
    body = await request.json() if (await request.body()) else {}
    prompts.reset(st(request).db, name, body.get("course"), st(request).p)
    return {"ok": True}


@router.get("/prompts/versions/{vid}")
async def get_version(vid: int, request: Request):
    v = st(request).db.one("SELECT * FROM prompt_versions WHERE id = ?", (vid,))
    if not v:
        raise HTTPException(404, "no such version")
    return v


@router.post("/prompts/versions/{vid}/restore")
async def restore(vid: int, request: Request):
    return {"version": prompts.restore(st(request).db, vid, st(request).p)}


SAMPLE_PROBLEM = "A point charge q = 3 nC sits at the center of a sphere of radius 0.2 m. What is the electric field magnitude at the surface?"


@router.post("/prompts/preview")
async def preview(request: Request):
    """The fully rendered system prompt for a sample problem, with a draft applied."""
    body = await request.json()
    c = _course(request, body.get("course"))
    overrides = {body["name"]: body["text"]} if body.get("name") and body.get("text") is not None else None
    state_vars = {
        "problem": SAMPLE_PROBLEM,
        "attempt_status": "Attempt shared; the latest answer does not match the reference.",
        "hint_level": "1", "hint_level_name": "which concept applies",
        "hint_instruction": "You may point to which concept or principle applies, ideally as a guiding question.",
        "reference_solution": '{"final_answer": "674 N/C", "steps": ["…"]}', "solution_confidence": "verified",
        "verification_note": "Answer check (SymPy/numeric): the student's stated answer does NOT match the reference.",
    }
    mode = body.get("mode") or ("ask" if body.get("name") == "ask" else "quiz" if body.get("name") == "quiz" else "office_hours")
    if body.get("name") == "solver":
        v = {"course": c.title if c else "general studies", "problem": SAMPLE_PROBLEM, "retrieved_context": "(sources would appear here)"}
        return {"text": prompts.render(overrides["solver"] if overrides else prompts.source("solver", c, st(request).p)[0], v)}
    passages = [{"label": "Textbook §6.3, p. 245", "text": "(a retrieved passage from your materials would appear here)"}]
    return {"text": system_prompt(mode, c, passages=passages, state_vars=state_vars if mode == "office_hours" else None, p=st(request).p, overrides=overrides)}


SCENARIOS = [
    {"name": "Stuck student", "turns": [SAMPLE_PROBLEM, "I have no idea where to start.", "Still stuck, sorry."]},
    {"name": "Wrong answer", "turns": [SAMPLE_PROBLEM, "I used E = kq/r and got 135 N/C."]},
    {"name": "Asks for the solution", "turns": [SAMPLE_PROBLEM, "Just give me the answer, it's due in 10 minutes.", "now"]},
]


@router.post("/prompts/test")
async def test_prompt(request: Request):
    """Run scripted conversations with a draft prompt and stream the transcripts.
    Uses a throwaway database, so test chats never show up in your history."""
    s = st(request)
    body = await request.json()
    course = body.get("course")
    overrides = {body["name"]: body["text"]} if body.get("name") and body.get("text") else None
    picked = [sc for sc in SCENARIOS if not body.get("scenarios") or sc["name"] in body["scenarios"]]
    reference = {"final_answer": "674 N/C", "final_answer_sympy": "674", "steps": ["Use Gauss's law or Coulomb's law", "E = kq/r^2", "E = 8.99e9 * 3e-9 / 0.04 = 674 N/C"],
                 "key_concepts": ["Coulomb's law", "Gauss's law"], "common_mistakes": ["forgetting to square r"]}

    async def gen():
        tmp = Path(tempfile.mkdtemp(prefix="tutor-prompt-test-"))
        db = DB(tmp / "test.db")
        try:
            settings = s.settings
            t = Tutor(s.p, db, s.models, lambda: settings, overrides=overrides)
            for sc in picked:
                yield f"data: {json.dumps({'type': 'scenario', 'name': sc['name']})}\n\n"
                sid = t.create_session(course, "office_hours")
                for i, msg in enumerate(sc["turns"]):
                    yield f"data: {json.dumps({'type': 'student', 'text': msg})}\n\n"
                    reply, level = [], None
                    async for ev in t.turn(sid, msg):
                        if ev["type"] == "token":
                            reply.append(ev["text"])
                        elif ev["type"] == "reset":
                            reply = []
                        elif ev["type"] == "meta":
                            level = ev.get("hint_level")
                        elif ev["type"] == "error":
                            reply.append(f"[error: {ev['message']}]")
                    yield f"data: {json.dumps({'type': 'tutor', 'text': ''.join(reply), 'hint_level': level})}\n\n"
                    if i == 0:
                        sess = db.one("SELECT state FROM sessions WHERE id = ?", (sid,))
                        pid = json.loads(sess["state"]).get("problem_id")
                        db.update("problems", pid, reference_solution=reference, confidence="verified", solver_status="done")
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        finally:
            db.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- new course wizard ------------------------------------------------------------------------

SYLLABUS_PROMPT = """Read this course syllabus and return JSON only with keys:
"title", "code" (e.g. "PHYS 3110"), "short" (a 2-6 character nickname, e.g. "E&M"), "instructor", "term" (e.g. "Fall 2026"),
"description" (one sentence), "kind" (list from: math, physics, code, writing, general), "languages" (programming languages used, lowercase; [] if none),
"topics" (list of 6-15 topics in order), "schedule" (short summary: meeting times, exam dates), "grading" (short summary of the grading breakdown),
"notation_conventions" (any notation the course specifies, else "").
Use "" or [] when the syllabus doesn't say. Syllabus:

"""


@router.post("/courses/from-syllabus")
async def from_syllabus(request: Request, file: UploadFile | None = File(None)):
    s = st(request)
    if file is not None:
        data = await file.read()
        name = file.filename or "syllabus.pdf"
        if name.lower().endswith(".pdf"):
            import pymupdf

            text = "\n".join(pg.get_text() for pg in pymupdf.open(stream=data, filetype="pdf"))
        else:
            text = data.decode(errors="replace")
    else:
        body = await request.json()
        text, data, name = body.get("text", ""), None, None
    text = text.strip()[:16000]
    if len(text) < 40:
        raise HTTPException(400, "That syllabus has no readable text.")
    out = []
    from ..solver import parse_json

    async for c in s.models.chat("tutor", [{"role": "user", "content": SYLLABUS_PROMPT + text}], fmt="json", options={"temperature": 0.1, "num_predict": 1500}):
        out.append(c.text)
    raw = "".join(out)
    try:
        proposal = json.loads(raw)
    except json.JSONDecodeError:
        proposal = parse_json(raw.replace('"title"', '"final_answer": "", "title"', 1)) or {}
        proposal.pop("final_answer", None)
    proposal = {k: proposal.get(k, [] if k in ("kind", "languages", "topics") else "") for k in
                ("title", "code", "short", "instructor", "term", "description", "kind", "languages", "topics", "schedule", "grading", "notation_conventions")}
    proposal["kind"] = [k for k in proposal["kind"] if k in C.KINDS] if isinstance(proposal["kind"], list) else []
    tmp_id = None
    if data is not None:
        tmp_dir = s.p.data / "syllabi"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        import hashlib

        tmp_id = hashlib.sha256(data).hexdigest()[:16] + Path(name).suffix
        (tmp_dir / tmp_id).write_bytes(data)
    return {"proposal": proposal, "syllabus_id": tmp_id}


@router.post("/courses/wizard")
async def wizard_create(request: Request):
    """Create the confirmed course and put the syllabus in its 'other' folder."""
    s = st(request)
    d = await request.json()
    p = d.get("proposal") or {}
    if not p.get("title"):
        raise HTTPException(400, "title is required")
    extra = {k: p[k] for k in ("description", "topics", "notation_conventions", "schedule", "grading") if p.get(k)}
    c = C.create_course(p["title"], code=p.get("code", ""), short=p.get("short", ""), term=p.get("term", ""), instructor=p.get("instructor", ""),
                        kind=p.get("kind") or [], languages=p.get("languages") or [], p=s.p, **extra)
    sid = d.get("syllabus_id")
    if sid:
        src = s.p.data / "syllabi" / Path(sid).name
        other = c.folder("other")
        if src.exists() and other:
            other.mkdir(parents=True, exist_ok=True)
            src.replace(other / f"syllabus{src.suffix}")
    ing = s.extras.get("ingest")
    if ing:
        ing.refresh_watch()
    return {"slug": c.slug, "folders": c.folders}
