"""The Magnus focus timer: snapshot, commands (via `magnus timer`), focus minutes per course."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..courses import load_course
from ..timer_bridge import TimerBridge
from .events import bus

router = APIRouter(prefix="/api")


def bridge(request: Request) -> TimerBridge:
    return request.app.state.tutor.extras["timer_bridge"]


def on_startup(st):
    b = TimerBridge(lambda: st.settings, bus.publish)
    st.extras["timer_bridge"] = b
    b.start()


def on_shutdown(st):
    b = st.extras.get("timer_bridge")
    if b:
        b.stop()


@router.get("/timer")
async def get_timer(request: Request):
    return bridge(request).read()


@router.get("/timer/minutes")
async def minutes(request: Request, course: str, days: int = 30):
    s = request.app.state.tutor
    c = load_course(course, s.p)
    if not c:
        raise HTTPException(404, "no such course")
    sessions = s.db.one("SELECT COUNT(*) AS n FROM sessions WHERE course = ? AND focus_label IS NOT NULL", (course,))["n"]
    try:
        data = await bridge(request).minutes(f"office hours: {c.name}", days)
    except Exception as e:
        return {"minutes": None, "sessions": sessions, "error": str(e)[:200]}
    return {"minutes": data.get("minutes", 0), "focus_sessions": data.get("sessions", 0), "sessions": sessions, "by_label": data.get("by_label", {}), "days": days}


@router.post("/timer/{cmd}")
async def command(cmd: str, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    try:
        out = await bridge(request).command(cmd, label=body.get("label"), task=body.get("task"), none=bool(body.get("none")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "result": out, "timer": bridge(request).last}
