"""FastAPI app: localhost-only backend that serves the API and the built web app."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import runtime
from ..setup import bootstrap
from .state import AppState, make_state

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def create_app(state: AppState | None = None, *, manage_processes: bool = True) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        st: AppState = app.state.tutor
        bootstrap(st.p)
        if manage_processes:
            await asyncio.to_thread(runtime.ensure_ollama, st.settings, st.p)
        watchers = []
        for hook in app.state.startup_hooks:
            res = hook(st)
            if asyncio.iscoroutine(res):
                res = await res
            if res is not None:
                watchers.append(res)
        idle = asyncio.create_task(_idle_watch(app)) if manage_processes else None
        try:
            yield
        finally:
            if idle:
                idle.cancel()
            for w in watchers:
                if isinstance(w, asyncio.Task):
                    w.cancel()
            for hook in app.state.shutdown_hooks:
                with contextlib.suppress(Exception):
                    res = hook(st)
                    if asyncio.iscoroutine(res):
                        await res
            if manage_processes:
                with contextlib.suppress(Exception):
                    await st.models.ollama.unload_all()
                await asyncio.to_thread(runtime.stop_ollama, st.p)

    app = FastAPI(title="Magnus Tutor", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.tutor = state or make_state()
    app.state.startup_hooks = []
    app.state.shutdown_hooks = []

    @app.middleware("http")
    async def localhost_only(request: Request, call_next):
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return JSONResponse({"error": "localhost only"}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path not in ("/api/health", "/api/events"):
            app.state.tutor.touch()
        return await call_next(request)

    from . import routes_core, routes_courses, routes_sessions  # noqa: E402

    app.include_router(routes_core.router)
    app.include_router(routes_courses.router)
    app.include_router(routes_sessions.router)
    for mod in _optional_routers():
        app.include_router(mod.router)
        if hasattr(mod, "on_startup"):
            app.state.startup_hooks.append(mod.on_startup)
        if hasattr(mod, "on_shutdown"):
            app.state.shutdown_hooks.append(mod.on_shutdown)

    _mount_web(app)
    return app


def _optional_routers():
    """Routers added by later milestones; each module is self-contained."""
    import importlib

    mods = []
    for name in ("routes_timer", "routes_library", "routes_code", "routes_prompts", "routes_quiz", "routes_models"):
        try:
            mods.append(importlib.import_module(f"{__package__}.{name}"))
        except ModuleNotFoundError as e:
            if e.name != f"{__package__}.{name}":
                raise
    return mods


def _mount_web(app: FastAPI) -> None:
    if not WEB_DIST.exists():
        @app.get("/")
        async def no_web():
            return JSONResponse({"message": "Web app not built yet: cd web && npm install && npm run build"})
        return
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        f = WEB_DIST / path
        if path and f.is_file() and WEB_DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(WEB_DIST / "index.html")


async def _idle_watch(app: FastAPI) -> None:
    """Shut the backend down after `idle_shutdown_minutes` without activity.
    An open, visible web tab sends a heartbeat, so it counts as activity."""
    st: AppState = app.state.tutor
    while True:
        await asyncio.sleep(30)
        minutes = st.settings["server"].get("idle_shutdown_minutes") or 0
        if minutes and time.time() - st.last_activity > minutes * 60 and not st.models.foreground_active:
            os.kill(os.getpid(), signal.SIGTERM)
            return
