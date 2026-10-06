"""Health, hardware, settings, resources, events, shutdown."""

from __future__ import annotations

import asyncio
import os
import signal

import psutil
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .. import hardware
from ..config import save_settings
from ..hardware import GB
from .events import bus, sse

router = APIRouter(prefix="/api")


def st(request: Request):
    return request.app.state.tutor


@router.get("/health")
async def health():
    return {"ok": True}


@router.post("/heartbeat")
async def heartbeat(request: Request):
    """The web app pings this while its tab is visible (counts as activity)."""
    return {"ok": True}


@router.get("/hardware")
async def get_hardware():
    hw = hardware.detect()
    return {"hardware": hardware.as_dict(hw), "recommendation": hardware.recommend(hw), "catalogue": hardware.CATALOGUE}


@router.get("/settings")
async def get_settings(request: Request):
    s = st(request).settings
    return {**s, "cloud": {**s["cloud"], "key_present": _key_present()}}


@router.patch("/settings")
async def patch_settings(request: Request):
    changes = await request.json()
    changes.pop("hardware", None)
    s = st(request)
    save_settings(changes, s.p)
    s.reload_settings()
    if "alerts" in changes and s.extras.get("timer_bridge"):
        try:
            await s.extras["timer_bridge"].set_alerts(changes["alerts"])
        except Exception:
            pass  # Magnus not installed: the setting still applies in the web app
    if "cloud" in changes:
        from ..llm.cloud import configure_cloud

        configure_cloud(s)
    bus.publish("settings", {"changed": list(changes)})
    return await get_settings(request)


def _key_present() -> bool:
    try:
        from ..llm.cloud import get_api_key

        return get_api_key() is not None
    except Exception:
        return False


@router.get("/resources")
async def resources(request: Request):
    s = st(request)
    vm = psutil.virtual_memory()
    me = psutil.Process(os.getpid())
    jobs = s.db.all("SELECT id, kind, status, progress, message FROM jobs WHERE status IN ('running','queued','paused','needs_confirmation') ORDER BY id")
    return {
        "loaded_models": await s.models.ollama.loaded(),
        "memory": {"total_gb": round(vm.total / GB, 1), "available_gb": round(vm.available / GB, 1), "percent": vm.percent},
        "backend_mb": round(me.memory_info().rss / 2**20),
        "jobs": jobs,
        "on_battery": hardware.on_battery(),
        "background_paused": s.settings["background"].get("paused", False),
        "warnings": s.models.warnings[-5:],
        "cloud_enabled": bool(s.models.cloud),
        "ollama_up": await s.models.ollama.available(),
    }


@router.post("/models/unload")
async def unload(request: Request):
    return {"unloaded": await st(request).models.ollama.unload_all()}


@router.post("/shutdown")
async def shutdown():
    asyncio.get_running_loop().call_later(0.3, os.kill, os.getpid(), signal.SIGTERM)
    return {"ok": True}


@router.get("/events")
async def events(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            yield sse("hello", {"ok": True})
            while True:
                if await request.is_disconnected():
                    return
                try:
                    kind, data = await asyncio.wait_for(q.get(), timeout=25)
                    yield sse(kind, data)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
