"""`tutor doctor`: prove nothing is left running and show what the tutor costs.

Reports tutor/Ollama processes, loaded models, memory, disk use, and checks
that no launch agents, login items or cron jobs mention the tutor or Ollama.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import psutil

from .config import Paths, load_settings, paths
from .hardware import GB


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for f in path.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def processes() -> list[dict]:
    out = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        name = proc.info["name"] or ""
        kind = None
        if "magnus_tutor.server" in cmd:
            kind = "tutor backend"
        elif "magnus_tutor.ingest" in cmd or "magnus_tutor.worker" in cmd:
            kind = "tutor worker"
        elif name.startswith("ollama") or "/ollama" in cmd.split(" ")[0]:
            kind = "ollama runner" if ("runner" in cmd or "llama-server" in cmd) else "ollama server"
        elif name == "Ollama":
            kind = "Ollama app"
        if kind:
            rss = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
            out.append({"pid": proc.info["pid"], "kind": kind, "rss_mb": round(rss / 2**20), "cmd": cmd[:120]})
    return out


def loaded_models(host: str) -> list[dict]:
    try:
        r = httpx.get(f"{host}/api/ps", timeout=2)
        return [{"name": m["name"], "gb": round(m.get("size", 0) / GB, 1)} for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return []


def installed_models() -> list[dict]:
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            rows.append({"name": parts[0], "size": f"{parts[2]} {parts[3]}"})
    return rows


def startup_items() -> list[str]:
    """Anything that would start the tutor or Ollama without being asked."""
    found = []
    for d in [Path.home() / "Library/LaunchAgents", Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons")]:
        if d.exists():
            for f in d.iterdir():
                if any(k in f.name.lower() for k in ("ollama", "magnus-tutor", "magnus_tutor", "tutor")):
                    found.append(str(f))
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5).stdout
        for line in cron.splitlines():
            if any(k in line.lower() for k in ("ollama", "tutor")):
                found.append(f"crontab: {line.strip()}")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        items = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get the name of every login item'],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for name in items.split(","):
            if any(k in name.lower() for k in ("ollama", "tutor")):
                found.append(f"login item: {name.strip()}")
    except (OSError, subprocess.SubprocessError):
        pass
    return found


def report(p: Paths | None = None) -> dict:
    p = p or paths()
    settings = load_settings(p)
    vm = psutil.virtual_memory()
    procs = processes()
    return {
        "processes": procs,
        "loaded_models": loaded_models(settings["ollama"]["host"]),
        "installed_models": installed_models(),
        "memory": {"total_gb": round(vm.total / GB, 1), "used_gb": round((vm.total - vm.available) / GB, 1), "available_gb": round(vm.available / GB, 1)},
        "disk": {
            "ollama_models_gb": round(_dir_size(Path.home() / ".ollama" / "models") / GB, 2),
            "tutor_data_mb": round(_dir_size(p.data) / 2**20, 1),
            "tutor_cache_mb": round(_dir_size(p.cache) / 2**20, 1),
        },
        "startup_items": startup_items(),
        "ollama_app_installed": Path("/Applications/Ollama.app").exists(),
    }


def format_report(r: dict) -> str:
    lines = ["Magnus Tutor doctor", ""]
    if r["processes"]:
        lines.append("Running:")
        lines += [f"  {x['kind']:<14} pid {x['pid']:<7} {x['rss_mb']:>6} MB  {x['cmd']}" for x in r["processes"]]
    else:
        lines.append("Running: nothing (no tutor backend, no Ollama)")
    lines.append("Loaded models: " + (", ".join(f"{m['name']} ({m['gb']} GB)" for m in r["loaded_models"]) or "none"))
    m = r["memory"]
    lines.append(f"Memory: {m['used_gb']} / {m['total_gb']} GB used, {m['available_gb']} GB available")
    d = r["disk"]
    lines.append(f"Disk: models {d['ollama_models_gb']} GB · tutor data {d['tutor_data_mb']} MB (cache {d['tutor_cache_mb']} MB)")
    if r["installed_models"]:
        lines.append("Installed models: " + ", ".join(f"{x['name']} ({x['size']})" for x in r["installed_models"]))
    lines.append("Startup items: " + (", ".join(r["startup_items"]) or "none (no launch agents, login items or cron jobs)"))
    if r["ollama_app_installed"]:
        lines.append("Ollama.app is installed: turn off its start-at-login in Ollama → Settings, or System Settings → General → Login Items.")
    return "\n".join(lines)
