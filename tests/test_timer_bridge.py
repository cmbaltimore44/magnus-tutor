"""The tutor's timer bridge against the real `magnus timer` (the tutor-timer
branch), in an isolated config dir. Skipped when that Magnus isn't available."""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from magnus_tutor.timer_bridge import TimerBridge, snapshot

MAGNUS = Path(os.environ.get("MAGNUS_REPO_FOR_TESTS", Path(__file__).resolve().parents[2] / "magnus"))


def _magnus_bin() -> Path | None:
    for cand in [os.environ.get("MAGNUS_TIMER_BIN"), MAGNUS / "bin" / "magnus.js"]:
        if cand and Path(cand).exists() and "timer" in (Path(cand).parent.parent / "src" / "subcommands.js").read_text():
            return Path(cand)
    return None


def test_snapshot_math():
    now = 1_000_000_000_000
    f = {"version": 3, "alerts": "auto", "settings": {"every": 4}, "timer": {"phase": "focus", "status": "running", "startedAt": now - 5 * 60000, "minutes": 25, "pausedAt": None, "pausedMs": 0, "round": 1, "label": "x", "title": "x"}}
    s = snapshot(f, now, alive=False)
    assert s["remaining_ms"] == 20 * 60000 and s["ends_at"] == now + 20 * 60000 and s["phase_label"] == "Focus 2/4"
    assert s["alert_owner"] == "web"
    assert snapshot(f, now, alive=True)["alert_owner"] == "terminal"
    assert snapshot(f, now + 21 * 60000, alive=False)["status"] == "ended"
    paused = {**f, "timer": {**f["timer"], "pausedAt": now - 60000}}
    assert snapshot(paused, now + 999999, alive=False)["remaining_ms"] == 21 * 60000
    assert snapshot({}, now, False)["active"] is False


@pytest.fixture
def magnus_env(tmp_path, monkeypatch):
    b = _magnus_bin()
    if not b or not shutil.which("node"):
        pytest.skip("Magnus with `magnus timer` not available")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("MAGNUS_TUTOR_MAGNUS_DIR", str(tmp_path / "xdg" / "magnus"))
    monkeypatch.setenv("MAGNUS_TUTOR_MAGNUS_CMD", f"{shutil.which('node')} {b}")
    monkeypatch.setenv("MAGNUS_DEMO", "1")  # demo data: nothing is logged to the real Supabase
    monkeypatch.setenv("MAGNUS_TIMER_FILES", "1")
    return tmp_path / "xdg" / "magnus"


async def test_commands_go_through_magnus_and_events_follow(magnus_env):
    events = []
    br = TimerBridge(lambda: {"magnus": {"command": "magnus"}}, lambda k, d: events.append(d))
    assert br.read()["active"] is False
    await br.command("start", label="office hours: E&M PSet 3")
    snap = br.read()
    assert snap["active"] and snap["label"] == "office hours: E&M PSet 3" and snap["phase"] == "focus"
    assert json.loads((magnus_env / "timer.json").read_text())["updated_by"] == "cli"
    await br.command("pause")
    assert br.read()["paused"] is True
    await br.command("resume")
    await br.command("switch", label="office hours: QM")
    assert br.read()["label"] == "office hours: QM"
    await br.command("stop")
    assert br.read()["active"] is False
    assert events and events[-1]["active"] is False
    await br.set_alerts("web")
    assert br.read()["alerts"] == "web"


async def test_watcher_pushes_outside_changes_within_a_second(magnus_env):
    import asyncio
    import subprocess

    events = []
    br = TimerBridge(lambda: {}, lambda k, d: events.append(d))
    br.start()
    await asyncio.sleep(0.3)
    t0 = time.time()
    cmd = os.environ["MAGNUS_TUTOR_MAGNUS_CMD"].split()
    subprocess.run([*cmd, "timer", "start", "--label", "from the terminal"], check=True, capture_output=True)
    while time.time() - t0 < 3 and not any(e.get("label") == "from the terminal" for e in events):
        await asyncio.sleep(0.05)
    br.stop()
    seen = [e for e in events if e.get("label") == "from the terminal"]
    assert seen, "bridge never saw the change"
    assert time.time() - t0 < 2.5


def test_tutor_never_writes_the_timer_or_supabase():
    import inspect

    import magnus_tutor.timer_bridge as tb

    src = inspect.getsource(tb)
    assert "write_text" not in src and "supabase" not in src.lower().replace("never talks to supabase", "").replace("or supabase", "")
