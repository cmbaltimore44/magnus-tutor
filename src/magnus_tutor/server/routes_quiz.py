"""Quiz mode: next question from your materials, grading against sources."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from .. import store
from ..courses import load_course
from ..llm.base import ProviderError
from ..quiz import Quizzer

router = APIRouter(prefix="/api")


def _q(request: Request) -> Quizzer:
    s = request.app.state.tutor
    if "quizzer" not in s.extras:
        s.extras["quizzer"] = Quizzer(s.p, s.db, s.models, s.extras.get("retriever"))
    return s.extras["quizzer"]


@router.get("/sessions/{sid}/quiz")
async def items(sid: int, request: Request):
    rows = request.app.state.tutor.db.all("SELECT * FROM quiz_items WHERE session_id = ? ORDER BY id", (sid,))
    for r in rows:
        r["source"] = json.loads(r["source"]) if r["source"] else None
        if r["grade"] is None:
            r["answer"] = None  # never shown before the student answers
    return rows


@router.post("/sessions/{sid}/quiz/next")
async def next_question(sid: int, request: Request):
    s = request.app.state.tutor
    sess = store.get_session(s.db, sid)
    if not sess:
        raise HTTPException(404, "no such session")
    body = await request.json() if await request.body() else {}
    course = load_course(sess["course"], s.p) if sess["course"] else None
    try:
        return await _q(request).next_question(sid, course, body.get("topic"))
    except (RuntimeError, ProviderError) as e:
        raise HTTPException(502, str(e))


@router.post("/sessions/{sid}/quiz/answer")
async def answer(sid: int, request: Request):
    s = request.app.state.tutor
    body = await request.json()
    sess = store.get_session(s.db, sid)
    course = load_course(sess["course"], s.p) if sess and sess["course"] else None
    try:
        return await _q(request).grade(int(body["item_id"]), body.get("answer", ""), course)
    except KeyError:
        raise HTTPException(404, "no such question")
    except ProviderError as e:
        raise HTTPException(502, str(e))
