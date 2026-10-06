"""Bridge to Magnus's focus timer (docs/timer-contract.md).

Reads ~/.config/magnus/timer.json (never writes it), watches it with OS file
events (no polling), and pushes snapshots to the browser. Commands go through
`magnus timer … --json`, so Magnus does all the writing and all the focus
logging. The tutor never touches focus_sessions or Supabase.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import time
from pathlib import Path

PHASE_LABELS = {"focus": "Focus", "short": "Break", "long": "Long break"}
COMMANDS = {"start", "pause", "resume", "skip", "add5", "switch", "stop", "discard"}


def magnus_dir() -> Path:
    override = os.environ.get("MAGNUS_TUTOR_MAGNUS_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "magnus"


def magnus_command(settings: dict) -> list[str]:
    cmd = os.environ.get("MAGNUS_TUTOR_MAGNUS_CMD") or settings.get("magnus", {}).get("command", "magnus")
    parts = shlex.split(cmd)
    if parts and not os.path.isabs(parts[0]):
        found = shutil.which(parts[0]) or (f"/opt/homebrew/bin/{parts[0]}" if Path(f"/opt/homebrew/bin/{parts[0]}").exists() else None)
        if found:
            parts[0] = found
    return parts


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def tui_alive(d: Path, now_ms: float | None = None) -> bool:
    hb = _read_json(d / "tui.json")
    if not hb:
        return False
    now_ms = now_ms or time.time() * 1000
    if now_ms - hb.get("heartbeat_at", 0) > 45000:
        return False
    try:
        os.kill(int(hb["pid"]), 0)
        return True
    except (OSError, ValueError, KeyError):
        return False


def snapshot(file: dict | None, now_ms: float, alive: bool) -> dict:
    """Same computation as Magnus's timerActions.snapshot (timestamps, no ticking)."""
    file = file or {}
    s = {"focus": 25, "short": 5, "long": 15, "every": 4, **(file.get("settings") or {})}
    alerts = file.get("alerts", "auto")
    owner = "terminal" if alerts == "terminal" else "both" if alerts == "both" else "web" if alerts == "web" else ("terminal" if alive else "web")
    base = {"version": file.get("version", 0), "alerts": alerts, "alert_owner": owner, "tui_alive": alive, "every": s["every"], "available": True, "computed_at": now_ms}
    t = file.get("timer")
    if not t:
        return {"active": False, **base}
    if not t.get("phase"):
        t = {**t, "phase": "focus", "round": 0, "status": "running"}
    minutes_ms = t.get("minutes", 25) * 60000
    end = t.get("pausedAt") or now_ms
    elapsed = min(minutes_ms, max(0, end - t.get("startedAt", now_ms) - (t.get("pausedMs") or 0)))
    remaining = minutes_ms - elapsed
    status = t.get("status", "running")
    if status == "running" and not t.get("pausedAt") and remaining <= 0:
        status = "ended"
    if status == "ended":
        remaining = 0
    phase = t["phase"]
    label = f"Focus {min(t.get('round', 0) + 1, s['every'])}/{s['every']}" if phase == "focus" else PHASE_LABELS.get(phase, phase)
    return {
        "active": True,
        "phase": phase,
        "status": status,
        "paused": bool(t.get("pausedAt")),
        "remaining_ms": int(remaining),
        "ends_at": int(now_ms + remaining) if status == "running" and not t.get("pausedAt") else None,
        "phase_label": label,
        "round": t.get("round", 0),
        "minutes": t.get("minutes"),
        "started_at": t.get("startedAt"),
        "title": t.get("title"),
        "label": t.get("label"),
        "task_id": t.get("taskId"),
        **base,
    }


class TimerBridge:
    def __init__(self, settings, publish):
        self.settings = settings  # callable → settings dict
        self.publish = publish
        self.dir = magnus_dir()
        self.last: dict = {"active": False}
        self._task: asyncio.Task | None = None
        self._ender: asyncio.Task | None = None

    # --- reading -------------------------------------------------------------------------

    def read(self) -> dict:
        snap = snapshot(_read_json(self.dir / "timer.json"), time.time() * 1000, tui_alive(self.dir))
        cmd = magnus_command(self.settings())
        if not cmd or not (os.path.isabs(cmd[0]) and Path(cmd[0]).exists()):
            snap["available"] = False
            snap["error"] = "The magnus command wasn't found (set magnus.command in settings.yaml)."
        return snap

    def refresh(self) -> dict:
        self.last = self.read()
        self.publish("timer", self.last)
        self._schedule_phase_end()
        return self.last

    def _schedule_phase_end(self) -> None:
        """No file changes when a phase simply runs out, so push one snapshot at the end time."""
        if self._ender:
            self._ender.cancel()
        ends = self.last.get("ends_at")
        if ends:
            delay = max(0.0, ends / 1000 - time.time()) + 0.3
            self._ender = asyncio.get_event_loop().create_task(self._at(delay))

    async def _at(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self.refresh()

    async def watch(self) -> None:
        from watchfiles import awatch

        self.dir.mkdir(parents=True, exist_ok=True)
        self.refresh()
        async for changes in awatch(self.dir, recursive=False, debounce=50, step=50):
            if any(Path(p).name in ("timer.json", "tui.json") for _, p in changes):
                self.refresh()

    def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self.watch())

    def stop(self) -> None:
        for t in (self._task, self._ender):
            if t:
                t.cancel()

    # --- commands ------------------------------------------------------------------------

    async def command(self, name: str, label: str | None = None, task: str | None = None, none: bool = False) -> dict:
        if name not in COMMANDS:
            raise ValueError(f"unknown timer command {name}")
        args = [*magnus_command(self.settings()), "timer", name]
        if task:
            args += ["--task", task]
        elif label:
            args += ["--label", label[:120]]
        elif none and name == "switch":
            args += ["--none"]
        args.append("--json")
        out = await self._run(args)
        self.refresh()
        return out

    async def set_alerts(self, mode: str) -> dict:
        return await self._run([*magnus_command(self.settings()), "timer", "alerts", mode, "--json"])

    async def minutes(self, label_prefix: str, days: int = 30) -> dict:
        return await self._run([*magnus_command(self.settings()), "timer", "minutes", "--label", label_prefix, "--days", str(days), "--json"])

    async def _run(self, args: list[str]) -> dict:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise RuntimeError("magnus timer timed out")
        text = out.decode().strip().splitlines()
        data = {}
        if text:
            with contextlib.suppress(json.JSONDecodeError):
                data = json.loads(text[-1])
        if proc.returncode != 0:
            raise RuntimeError(err.decode().strip() or f"magnus timer exited {proc.returncode}")
        return data

    # --- linking tutor sessions to focus rounds --------------------------------------------

    def current_focus(self) -> dict | None:
        s = self.read()
        if s.get("active") and s.get("phase") == "focus":
            return {"label": s.get("label") or s.get("title"), "started_at": (s.get("started_at") or 0) / 1000}
        return None

    def now_if_focus(self) -> float | None:
        return time.time() if self.current_focus() else None
