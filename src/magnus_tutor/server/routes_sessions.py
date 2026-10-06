"""Sessions, messages (streamed over SSE), uploads, and a plain-text ask for Magnus."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .. import store
from ..context import system_prompt
from ..courses import load_course
from ..engine.ladder import GateState
from ..engine.tutor import Tutor

router = APIRouter(prefix="/api")


def st(request: Request):
    return request.app.state.tutor


def tutor_for(s) -> Tutor:
    t = s.extras.get("tutor")
    if t is None:
        t = Tutor(s.p, s.db, s.models, lambda: s.settings, retriever=s.extras.get("retriever"), solver=s.extras.get("solver"), ingest=s.extras.get("ingest"))
        s.extras["tutor"] = t
    return t


def _sse(ev: dict) -> str:
    return f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"


@router.post("/sessions")
async def create_session(request: Request):
    s = st(request)
    d = await request.json()
    mode = d.get("mode", "office_hours")
    if mode not in ("office_hours", "ask", "code", "quiz"):
        raise HTTPException(400, "bad mode")
    focus = None
    timer = s.extras.get("timer_bridge")
    if timer:
        focus = timer.current_focus()
    sid = tutor_for(s).create_session(d.get("course"), mode, d.get("title"), focus)
    return await get_session(sid, request)


@router.get("/sessions")
async def list_sessions(request: Request, course: str | None = None, limit: int = 30):
    return store.list_sessions(st(request).db, course, limit)


@router.get("/sessions/{sid}")
async def get_session(sid: int, request: Request):
    s = st(request)
    sess = store.get_session(s.db, sid)
    if not sess:
        raise HTTPException(404, "no such session")
    state = GateState.from_dict(sess["state"])
    pr = store.get_problem(s.db, state.problem_id)
    return {
        "session": sess,
        "messages": store.messages(s.db, sid),
        "problem": store.public_problem(pr, state.solution_unlocked or state.solved),
        "state": state.to_dict(),
    }


@router.post("/sessions/{sid}/messages")
async def post_message(sid: int, request: Request):
    s = st(request)
    d = await request.json()
    if not (d.get("text") or d.get("images") or d.get("action")):
        raise HTTPException(400, "empty message")
    t = tutor_for(s)

    async def gen():
        async for ev in t.turn(sid, d.get("text", ""), d.get("images") or [], action=d.get("action"),
                               code_context=d.get("code_context"), provider=d.get("provider", "ollama")):
            yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.patch("/sessions/{sid}")
async def patch_session(sid: int, request: Request):
    s = st(request)
    d = await request.json()
    if "title" in d:
        s.db.execute("UPDATE sessions SET title = ? WHERE id = ?", (d["title"], sid))
    return await get_session(sid, request)


@router.post("/sessions/{sid}/end")
async def end_session(sid: int, request: Request):
    s = st(request)
    timer = s.extras.get("timer_bridge")
    store.end_session(s.db, sid, timer.now_if_focus() if timer else None)
    return {"ok": True}


@router.delete("/sessions/{sid}")
async def delete_session(sid: int, request: Request):
    st(request).db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    return {"ok": True}


@router.post("/uploads")
async def upload(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 25 * 2**20:
        raise HTTPException(413, "image too large (25 MB max)")
    return {"id": store.save_upload(st(request).p, data, file.filename or "image.png")}


@router.get("/uploads/{upload_id}")
async def get_upload(upload_id: str, request: Request):
    f = store.upload_path(st(request).p, upload_id)
    if not f:
        raise HTTPException(404, "no such upload")
    return FileResponse(f)


@router.post("/ask")
async def ask_plain(request: Request):
    """Plain-text streaming ask for the Magnus terminal (math stays as plain text/LaTeX).
    Reuses a session if `session_id` is given; the id is returned in X-Session-Id."""
    s = st(request)
    d = await request.json()
    t = tutor_for(s)
    sid = d.get("session_id") or t.create_session(d.get("course"), "ask", (d.get("text") or "")[:60])

    async def gen():
        async for ev in t.turn(sid, d.get("text", "")):
            if ev["type"] == "token":
                yield ev["text"]
            elif ev["type"] == "error":
                yield f"\n[error] {ev['message']}\n"
            elif ev["type"] == "done":
                srcs = [x["label"] for x in (store.messages(s.db, sid)[-1]["meta"].get("sources") or [])]
                if srcs:
                    yield "\n\nSources: " + "; ".join(srcs)
                yield "\n"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8", headers={"X-Session-Id": str(sid)})


@router.post("/chat")
async def chat(request: Request):
    """Stateless chat with context injected (milestone 1; used by `tutor chat` tests)."""
    s = st(request)
    body = await request.json()
    course = load_course(body["course"], s.p) if body.get("course") else None
    messages = [{"role": "system", "content": system_prompt("ask", course, p=s.p)}, *body.get("messages", [])]

    async def gen():
        async for c in s.models.chat("tutor", messages):
            if c.text:
                yield f"data: {json.dumps({'text': c.text})}\n\n"
            if c.done:
                yield f"event: done\ndata: {json.dumps(c.stats)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/problems/{pid}")
async def get_problem(pid: int, request: Request):
    s = st(request)
    pr = store.get_problem(s.db, pid)
    if not pr:
        raise HTTPException(404, "no such problem")
    unlocked = bool(s.db.one(
        "SELECT 1 FROM sessions WHERE json_extract(state, '$.problem_id') = ? AND (json_extract(state, '$.solution_unlocked') OR json_extract(state, '$.solved'))",
        (pid,),
    ))
    return store.public_problem(pr, unlocked)


@router.post("/problems/{pid}/solve")
async def solve_problem(pid: int, request: Request):
    """Run the hidden solver on demand (Light preset, or to retry)."""
    solver = st(request).extras.get("solver")
    if not solver:
        raise HTTPException(503, "solver unavailable")
    solver.start(pid)
    return {"ok": True}
