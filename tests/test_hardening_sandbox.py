"""Sandboxed programs can't start subprocesses, and the solver's model-written
reference code only runs when you turn it on."""

import shutil

import pytest

from magnus_tutor import sandbox
from magnus_tutor.tools.verify import verify

needs_sandbox = pytest.mark.skipif(shutil.which("sandbox-exec") is None, reason="macOS sandbox-exec required")
FAST = {"timeout_s": 4, "cpu_s": 4, "memory_mb": 256}


@needs_sandbox
@pytest.mark.parametrize(
    "code",
    [
        "import os\nos.fork()\nprint('FORKED')",
        "import subprocess\nsubprocess.run(['/bin/echo', 'x'])\nprint('SPAWNED')",
        "import os\nos.posix_spawn('/bin/echo', ['echo', 'x'], {})\nprint('SPAWNED')",
        "import os\nif os.fork() == 0:\n    os.setsid()\n    os.fork()\nprint('FORKED')",
        "import os\nfor _ in range(100):\n    os.fork()\nprint('FORKED')",
    ],
)
def test_programs_cannot_start_processes(p, code):
    r = sandbox.run({"main.py": code}, "python", limits=FAST, p=p)
    assert "FORKED" not in r.stdout and "SPAWNED" not in r.stdout
    assert r.exit_code != 0 and ("Operation not permitted" in r.stderr or "PermissionError" in r.stderr)


@needs_sandbox
def test_allow_fork_is_opt_in(p):
    spec = {"python": {"extensions": [".py"], "requires": "{python}", "run": "{python} {file}", "allow_fork": True}}
    p.languages.write_text(__import__("yaml").safe_dump(spec))
    r = sandbox.run({"main.py": "import subprocess\nprint(subprocess.run(['/bin/echo', 'child'], capture_output=True, text=True).stdout.strip())"},
                    "python", limits=FAST, p=p)
    assert r.stdout.strip() == "child", r.stderr


@needs_sandbox
def test_every_default_language_still_runs(p):
    results = {r["language"]: r for r in sandbox.check_languages(p)}
    for lang in ("python", "sml", "java", "ruby", "dockerfile"):
        assert results[lang]["ok"], results[lang]


@needs_sandbox
def test_single_command_runs_still_work(p):
    r = sandbox.run({"main.py": "print(input()[::-1])"}, "python", stdin="abc", limits=FAST, p=p)
    assert r.stdout.strip() == "cba" and r.exit_code == 0


CODE_REF = {
    "final_answer": "10",
    "language": "python",
    "reference_code": "print(sum(int(x) for x in input().split()))",
    "tests": [{"name": "t", "stdin": "1 2 3 4", "expected": "10"}],
    "checks": [],
}


def test_reference_code_does_not_run_by_default(p, monkeypatch):
    ran = []
    monkeypatch.setattr("magnus_tutor.tools.verify.code_checks", lambda ref, p=None: ran.append(1) or [{"kind": "code", "ok": True}])
    v = verify(CODE_REF, "code", p)
    assert ran == [] and v["status"] == "unverified"
    v = verify(CODE_REF, "code", p, run_code=True)
    assert ran == [1] and v["status"] == "verified"


def test_setting_defaults_off_and_is_editable(p):
    from magnus_tutor.config import load_settings
    from magnus_tutor.server.routes_core import editable_settings

    assert load_settings(p)["solver"]["run_reference_code"] is False
    assert editable_settings({"solver": {"run_reference_code": True}}) == {"solver": {"run_reference_code": True}}


async def test_solver_respects_the_setting(p, fake_models, monkeypatch):
    import json

    from magnus_tutor import store
    from magnus_tutor.db import get_db
    from magnus_tutor.config import load_settings, save_settings
    from magnus_tutor.solver import SolverService

    mm, fp = fake_models
    fp.reply = lambda messages, **kw: json.dumps(CODE_REF)
    calls = []
    monkeypatch.setattr("magnus_tutor.tools.verify.code_checks", lambda ref, p=None: calls.append(1) or [{"kind": "code", "ok": True}])
    db = get_db(p)
    pid = store.create_problem(db, None, "Sum the numbers on stdin.", kind="code")
    await SolverService(p, db, mm, lambda: load_settings(p)).solve(pid)
    assert calls == []
    save_settings({"solver": {"run_reference_code": True}}, p)
    pid2 = store.create_problem(db, None, "Sum the numbers on stdin.", kind="code")
    await SolverService(p, db, mm, lambda: load_settings(p)).solve(pid2)
    assert calls and store.get_problem(db, pid2)["confidence"] == "verified"
