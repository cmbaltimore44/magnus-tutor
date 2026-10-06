"""Process management: the tutor backend and the Ollama server it starts.

Nothing here installs a launch agent, login item or cron job. The backend runs
only after `tutor start` (or `magnus tutor`), shuts itself down when idle, and
`tutor stop` stops it plus any `ollama serve` it started.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

from .config import Paths, load_settings, paths

OLLAMA_ENV = {
    "OLLAMA_HOST": "127.0.0.1:11434",
    # The embedding model (~0.6B) may stay alongside one chat model; the model
    # manager makes sure only one *large* model is ever loaded.
    "OLLAMA_MAX_LOADED_MODELS": "2",
    "OLLAMA_NUM_PARALLEL": "1",
    "OLLAMA_MAX_QUEUE": "16",
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_KV_CACHE_TYPE": "q8_0",
    "OLLAMA_NOPRUNE": "0",
}


def _pidfile(p: Paths, name: str) -> Path:
    return p.run / f"{name}.json"


def _read(p: Paths, name: str) -> dict | None:
    try:
        return json.loads(_pidfile(p, name).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write(p: Paths, name: str, info: dict) -> None:
    p.run.mkdir(parents=True, exist_ok=True)
    tmp = _pidfile(p, name).with_suffix(".tmp")
    tmp.write_text(json.dumps(info))
    os.replace(tmp, _pidfile(p, name))


def _clear(p: Paths, name: str) -> None:
    _pidfile(p, name).unlink(missing_ok=True)


def _alive(pid: int | None, must_contain: str) -> psutil.Process | None:
    if not pid:
        return None
    try:
        proc = psutil.Process(pid)
        cmd = " ".join(proc.cmdline())
        return proc if must_contain in cmd and proc.status() != psutil.STATUS_ZOMBIE else None
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


# --- Ollama ------------------------------------------------------------------


def ollama_up(host: str) -> bool:
    try:
        return httpx.get(f"{host.rstrip('/')}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_ollama(settings: dict | None = None, p: Paths | None = None) -> dict:
    """Make sure an Ollama server is reachable. Starts one (with our limits) if needed."""
    p = p or paths()
    settings = settings or load_settings(p)
    o = settings["ollama"]
    if ollama_up(o["host"]):
        mine = _read(p, "ollama")
        return {"running": True, "managed": bool(mine and _alive(mine.get("pid"), "ollama"))}
    if not o.get("manage_server", True):
        return {"running": False, "managed": False, "error": "Ollama isn't running and manage_server is off"}
    exe = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
    if not Path(exe).exists():
        return {"running": False, "managed": False, "error": "ollama not installed (brew install ollama)"}
    env = {**os.environ, **OLLAMA_ENV, "OLLAMA_KEEP_ALIVE": str(o["keep_alive"]), "OLLAMA_CONTEXT_LENGTH": str(o["num_ctx"])}
    p.run.mkdir(parents=True, exist_ok=True)
    log = open(p.run / "ollama.log", "ab")
    proc = subprocess.Popen([exe, "serve"], env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    _write(p, "ollama", {"pid": proc.pid, "started": time.time()})
    for _ in range(60):
        if ollama_up(o["host"]):
            return {"running": True, "managed": True, "pid": proc.pid}
        time.sleep(0.25)
    return {"running": False, "managed": True, "error": "ollama serve didn't come up (see run/ollama.log)"}


def unload_models(host: str) -> list[str]:
    try:
        ps = httpx.get(f"{host}/api/ps", timeout=3).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return []
    names = [m["name"] for m in ps]
    for n in names:
        try:
            httpx.post(f"{host}/api/generate", json={"model": n, "keep_alive": 0}, timeout=30)
        except httpx.HTTPError:
            pass
    return names


def stop_ollama(p: Paths | None = None) -> bool:
    """Stop the `ollama serve` we started (never one the user started themselves)."""
    p = p or paths()
    info = _read(p, "ollama")
    proc = _alive(info.get("pid") if info else None, "ollama")
    _clear(p, "ollama")
    if not proc:
        return False
    children = proc.children(recursive=True)
    proc.terminate()
    _, still = psutil.wait_procs([proc, *children], timeout=8)
    for s in still:
        s.kill()
    return True


# --- tutor backend --------------------------------------------------------------


def server_url(settings: dict) -> str:
    return f"http://127.0.0.1:{settings['server']['port']}"


def server_up(settings: dict) -> bool:
    try:
        return httpx.get(f"{server_url(settings)}/api/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def start_server(p: Paths | None = None, wait: bool = True) -> dict:
    p = p or paths()
    settings = load_settings(p)
    if server_up(settings):
        return {"running": True, "url": server_url(settings), "already": True}
    p.run.mkdir(parents=True, exist_ok=True)
    log = open(p.run / "server.log", "ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "magnus_tutor.server"],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True, env={**os.environ},
    )
    _write(p, "server", {"pid": proc.pid, "port": settings["server"]["port"], "started": time.time()})
    if wait:
        for _ in range(120):
            if server_up(settings):
                return {"running": True, "url": server_url(settings), "pid": proc.pid}
            if proc.poll() is not None:
                return {"running": False, "error": f"backend exited ({proc.returncode}); see {p.run / 'server.log'}"}
            time.sleep(0.25)
        return {"running": False, "error": "backend didn't answer in 30 s"}
    return {"running": True, "url": server_url(settings), "pid": proc.pid}


def stop_server(p: Paths | None = None) -> bool:
    p = p or paths()
    info = _read(p, "server")
    proc = _alive(info.get("pid") if info else None, "magnus_tutor")
    _clear(p, "server")
    if not proc:
        return False
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(10)
    except psutil.TimeoutExpired:
        proc.kill()
    return True


def stop_all(p: Paths | None = None) -> dict:
    p = p or paths()
    settings = load_settings(p)
    info = _read(p, "ollama")
    managed = bool(info and _alive(info.get("pid"), "ollama"))
    unloaded = unload_models(settings["ollama"]["host"]) if ollama_up(settings["ollama"]["host"]) else []
    server = stop_server(p)  # the backend stops its own Ollama on the way out
    stopped = stop_ollama(p)
    return {"server": server, "unloaded": unloaded, "ollama": managed or stopped}
