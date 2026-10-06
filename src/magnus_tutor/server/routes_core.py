"""Health, hardware, settings, resources, events, shutdown."""

from __future__ import annotations

import asyncio
import os
import re
import signal

import psutil
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .. import hardware
from ..config import DEFAULT_SETTINGS, save_settings
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
    changes = editable_settings(await request.json())
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


# What the web app may change. Commands, hosts, ports and paths are only editable in
# settings.yaml by hand, so nothing reaching the API can point the backend at a program
# or a server of its choosing.
EDITABLE = {
    "preset": None, "models": None, "gates": None, "background": None, "alerts": None, "appearance": None, "ingest": None,
    "solver": {"enabled", "thinking_budget", "self_consistency", "extra_run_if_unverified", "policy"},
    "cloud": {"enabled", "model"},
    "ollama": {"keep_alive"},
    "server": {"idle_shutdown_minutes"},
    "cache": {"max_mb"},
}


def _typed(changes: dict, defaults: dict) -> dict:
    """Keep only values whose type matches the default (so a bad PATCH can't break the backend)."""
    out = {}
    for k, v in changes.items():
        d = defaults.get(k)
        if isinstance(d, dict):
            if isinstance(v, dict):
                sub = _typed(v, d) if d else {kk: vv for kk, vv in v.items() if isinstance(vv, (str, int, float, bool))}
                if sub:
                    out[k] = sub
        elif d is None or isinstance(v, type(d)) or (isinstance(d, float) and isinstance(v, int)):
            if not (isinstance(d, (int, float)) and isinstance(v, bool)) or isinstance(d, bool):
                out[k] = v
    return out


def editable_settings(changes: dict) -> dict:
    out = {}
    for k, v in (changes or {}).items():
        if k not in EDITABLE:
            continue
        allowed = EDITABLE[k]
        if allowed is None or not isinstance(v, dict):
            if allowed is None:
                out[k] = v
            continue
        sub = {kk: vv for kk, vv in v.items() if kk in allowed}
        if sub:
            out[k] = sub
    out = _typed(out, DEFAULT_SETTINGS)
    if isinstance(out.get("models"), dict):
        out["models"] = {r: m for r, m in out["models"].items() if r in ("tutor", "solver", "coder", "vision", "embedding") and isinstance(m, str)
                         and re.fullmatch(r"[A-Za-z0-9._:/-]{1,80}", m)}
    return out


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
