"""Models (list, size before pulling, pull with progress, delete), review list,
mastery, and reading the resume into the profile."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import courses as C
from ..config import load_yaml, write_yaml_atomic
from ..models_cmd import download_size

router = APIRouter(prefix="/api")


def st(request: Request):
    return request.app.state.tutor


@router.get("/models")
async def models(request: Request):
    s = st(request)
    try:
        installed = await s.models.ollama.list_models()
    except Exception:
        installed = []
    return {"installed": installed, "roles": s.settings["models"], "loaded": await s.models.ollama.loaded()}


@router.get("/models/size")
async def model_size(name: str):
    return {"name": name, "size": await asyncio.to_thread(download_size, name)}


@router.post("/models/pull")
async def pull(request: Request):
    """Pull a model. The UI always shows the size and asks first."""
    s = st(request)
    name = (await request.json())["name"]

    async def gen():
        async for ev in s.models.ollama.pull(name):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.delete("/models/{name:path}")
async def delete_model(name: str, request: Request):
    s = st(request)
    if name in s.settings["models"].values():
        raise HTTPException(400, f"{name} is assigned to a role; pick another model for that role first")
    return {"deleted": await s.models.ollama.delete(name)}


@router.get("/review")
async def review(request: Request, course: str | None = None):
    """Concepts worth reviewing: big hints, unlocked solutions, unsolved problems (last 30 days)."""
    s = st(request)
    rows = s.db.all(
        """SELECT course, concept_tags, hint_level_reached, solved, used_full_solution, started_at FROM attempts
           WHERE started_at > strftime('%s','now') - 30*86400 AND (? IS NULL OR course = ?) ORDER BY started_at DESC""",
        (course, course),
    )
    seen, out = set(), []
    for r in rows:
        tags = json.loads(r["concept_tags"] or "[]")
        reason = "unlocked the solution" if r["used_full_solution"] else ("needed the next-step hint" if r["hint_level_reached"] >= 3 else ("not solved yet" if not r["solved"] else None))
        if not reason:
            continue
        for t in tags[:2]:
            key = (r["course"], t.lower())
            if key not in seen:
                seen.add(key)
                out.append({"course": r["course"], "concept": t, "reason": reason})
    return out[:20]


@router.get("/mastery")
async def mastery(request: Request, course: str):
    return st(request).db.all("SELECT concept, score, evidence FROM mastery WHERE course = ? ORDER BY score ASC, evidence DESC", (course,))


@router.post("/profile/resume")
async def resume_to_profile(request: Request):
    """Summarize resume.pdf (in the config folder, or profile.resume) into profile.resume_summary."""
    s = st(request)
    prof = C.load_profile(s.p)
    path = prof.get("resume") or str(s.p.config / "resume.pdf")
    from pathlib import Path

    f = Path(str(path)).expanduser()
    if not f.exists():
        raise HTTPException(404, f"No resume found at {f}. Put resume.pdf in {s.p.config}.")
    import fitz

    text = "\n".join(page.get_text() for page in fitz.open(f))[:12000]
    prompt = (
        "Summarize this student's resume in 3-4 sentences for a tutor: coursework, technical skills, "
        "programming languages, research or work experience. Plain text, no preamble.\n\n" + text
    )
    out = []
    async for c in s.models.chat("tutor", [{"role": "user", "content": prompt}], options={"temperature": 0.2}):
        out.append(c.text)
    data = load_yaml(s.p.profile, {}) or {}
    data["resume"] = str(f)
    data["resume_summary"] = "".join(out).strip()
    write_yaml_atomic(s.p.profile, data)
    return {"profile": data}
