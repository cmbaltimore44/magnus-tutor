"""Chat endpoints (milestone 1: a stateless chat with context injected)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..context import system_prompt
from ..courses import load_course

router = APIRouter(prefix="/api")


@router.post("/chat")
async def chat(request: Request):
    s = request.app.state.tutor
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
