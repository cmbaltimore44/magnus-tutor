"""The sandbox must contain infinite loops, memory blowups, fork bombs, and
file/network access attempts."""

import shutil
from pathlib import Path

import pytest

from magnus_tutor import sandbox

pytestmark = pytest.mark.skipif(shutil.which("sandbox-exec") is None, reason="macOS sandbox-exec required")

FAST = {"timeout_s": 3, "cpu_s": 3, "memory_mb": 256}


def test_runs_python_and_tests(p):
    r = sandbox.run({"main.py": "print(input()[::-1])"}, "python", tests=[{"name": "rev", "stdin": "abc", "expected": "cba"}, {"name": "bad", "stdin": "ab", "expected": "ab"}], p=p)
    assert [t["passed"] for t in r.tests] == [True, False]
    assert not r.ok


def test_infinite_loop_is_killed(p):
    r = sandbox.run({"main.py": "while True: pass"}, "python", limits=FAST, p=p)
    assert r.killed in ("timeout", "cpu") and r.duration_s < 8


def test_memory_blowup_is_killed(p):
    r = sandbox.run({"main.py": "x = []\nwhile True: x.append(bytearray(50_000_000))"}, "python", limits=FAST, p=p)
    assert r.killed == "memory" or r.exit_code != 0
    assert r.duration_s < 8


def test_fork_bomb_is_contained(p):
    code = "import os, time\nfor _ in range(200):\n    if os.fork() == 0:\n        time.sleep(30)\n        os._exit(0)\ntime.sleep(30)\n"
    r = sandbox.run({"main.py": code}, "python", limits={**FAST, "max_procs": 16}, p=p)
    assert r.killed in ("processes", "timeout") or r.exit_code != 0
    assert r.duration_s < 10


def test_cannot_read_home_or_write_outside(p):
    secret = Path.home() / ".zshrc"
    code = f"""
import os
try:
    open({str(secret)!r}).read(); print("READ_HOME")
except Exception as e: print("blocked read", type(e).__name__)
try:
    open("/tmp/tutor-sandbox-escape.txt", "w").write("x"); print("WROTE_OUTSIDE")
except Exception as e: print("blocked write", type(e).__name__)
open("inside.txt", "w").write("ok"); print("wrote inside")
"""
    r = sandbox.run({"main.py": code}, "python", limits=FAST, p=p)
    assert "READ_HOME" not in r.stdout and "WROTE_OUTSIDE" not in r.stdout
    assert "wrote inside" in r.stdout
    assert not Path("/tmp/tutor-sandbox-escape.txt").exists()


def test_no_network(p):
    code = "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 80), timeout=2); print('CONNECTED')\nexcept Exception as e: print('blocked', type(e).__name__)"
    r = sandbox.run({"main.py": code}, "python", limits=FAST, p=p)
    assert "CONNECTED" not in r.stdout and "blocked" in r.stdout


def test_sml_and_java(p):
    r = sandbox.run({"main.sml": 'fun fact 0 = 1 | fact n = n * fact (n - 1);\nval () = print (Int.toString (fact 5) ^ "\\n");'}, "sml", p=p)
    assert r.stdout.strip() == "120", r.stderr
    r = sandbox.run({"Main.java": 'public class Main { public static void main(String[] a) { System.out.println(6 * 7); } }'}, "java", p=p)
    assert r.stdout.strip() == "42", r.stderr


def test_launchservices_clipboard_and_keychain_are_blocked(p):
    code = """
import subprocess
for cmd in (["/usr/bin/open", "-g", "-j", "-b", "com.apple.systemevents"], ["/usr/bin/pbpaste"], ["/usr/bin/security", "show-keychain-info"], ["/usr/bin/osascript", "-e", "1"]):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=5)
        print(cmd[0], "rc", r.returncode)
    except Exception as e:
        print(cmd[0], "blocked", type(e).__name__)
"""
    r = sandbox.run({"main.py": code}, "python", limits=FAST, p=p)
    for line in r.stdout.splitlines():
        assert " rc 0" not in line, line


def test_detached_processes_do_not_survive(p):
    """A double-forked, setsid child escapes the process group; the run marker still gets it."""
    code = """
import os, time
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        print("CHILD", os.getpid(), flush=True)
        time.sleep(30)
        os._exit(0)
    os._exit(0)
time.sleep(0.3)
print("parent done")
"""
    r = sandbox.run({"main.py": code}, "python", limits=FAST, p=p)
    import psutil

    pid = int(r.stdout.split("CHILD", 1)[1].split()[0])
    assert "parent done" in r.stdout
    gone = not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    if not gone:
        psutil.Process(pid).kill()
    assert gone, "a detached child outlived its run"


def test_filenames_cannot_inject_shell(p):
    r = sandbox.run({"x$(touch /tmp/tutor-pwn).yaml": "a: 1"}, "kubernetes", p=p)
    assert r.exit_code == -1 and "File names" in r.stderr
    from pathlib import Path

    assert not Path("/tmp/tutor-pwn").exists()
