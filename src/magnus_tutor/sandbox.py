"""Code sandbox: run student or model code with hard limits.

Backends:
- subprocess (default): a temp dir, macOS `sandbox-exec` profile (no network,
  writes only inside the temp dir, no reading your home folder), rlimits (CPU
  seconds, file size, open files, no core dumps) and a watchdog that kills the
  whole process tree on wall-clock timeout, memory cap, or too many processes.
- docker (optional): only if Docker is already running and the setting asks
  for it. `--network none --memory --pids-limit --cpus`.

Python code runs in the sandbox's own venv (data/sandbox/python), never the
tutor's environment.
"""

from __future__ import annotations

import os
import re
import resource
import shlex
import uuid
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psutil

from . import languages as L
from .config import Paths, paths

DEFAULTS = {"timeout_s": 10, "cpu_s": 10, "memory_mb": 512, "max_procs": 32, "max_output": 64_000, "max_file_mb": 20}


@dataclass
class RunResult:
    language: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_s: float = 0.0
    timed_out: bool = False
    killed: str | None = None  # memory | processes | timeout | cpu
    tests: list[dict] = field(default_factory=list)
    mode: str = "run"
    backend: str = "subprocess"

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.killed and all(t.get("passed") for t in self.tests)

    def as_dict(self) -> dict:
        return {**asdict(self), "ok": self.ok}

    def summary(self) -> str:
        """Run result as text for the tutor prompt (ground truth for the model)."""
        lines = [f"[Run result: {self.language}, exit code {self.exit_code}{', killed: ' + self.killed if self.killed else ''}, {self.duration_s:.2f}s]"]
        if self.stdout:
            lines.append("stdout:\n" + _clip(self.stdout, 3000))
        if self.stderr:
            lines.append("stderr:\n" + _clip(self.stderr, 3000))
        for t in self.tests:
            mark = "PASS" if t["passed"] else "FAIL"
            lines.append(f"test {t['name']}: {mark}" + ("" if t["passed"] else f" (expected {t.get('expected')!r}, got {t.get('actual')!r})"))
        return "\n".join(lines)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"\n…[{len(s) - n} more characters]"


# --- the sandbox's own Python ------------------------------------------------------------------


def sandbox_python(p: Paths | None = None, create: bool = True) -> str:
    p = p or paths()
    venv = p.data / "sandbox" / "python"
    py = venv / "bin" / "python"
    if py.exists() or not create:
        return str(py)
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    venv.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([uv, "venv", "--python", "3.12", str(venv)], check=True, capture_output=True)
    subprocess.run([uv, "pip", "install", "--python", str(py), "numpy", "pytest"], check=False, capture_output=True)
    return str(py)


# --- the sandbox profile ------------------------------------------------------------------------------


def _profile(workdir: Path, extra_read: list[str], network: bool = False, extra_write: list[str] | None = None, allow_fork: bool = False) -> str:
    home = str(Path.home())
    reads = "\n".join(f'(allow file-read* (subpath "{x}"))' for x in [str(workdir), *extra_read])
    writes = "".join(f'(allow file-write* (subpath "{x}"))' for x in (extra_write or []))
    net = "" if network else "(deny network*)"
    # No fork at all: a sandboxed program can't spawn children, so nothing it starts can outlive
    # the run. (`sh -c` execs a simple command without forking.) A language that genuinely needs
    # subprocesses can opt in with `allow_fork: true` in languages.yaml.
    fork = "" if allow_fork else "(deny process-fork)"
    return f"""(version 1)
(allow default)
{net}
{fork}
(deny file-write*)
(allow file-write* (subpath "{workdir}"))
{writes}
(allow file-write* (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr") (literal "/dev/tty") (literal "/dev/dtracehelper"))
(deny file-read* (subpath "{home}"))
(deny file-read* (subpath "/Volumes"))
{reads}
(deny process-exec (literal "/usr/bin/open") (literal "/usr/bin/osascript") (literal "/bin/launchctl") (literal "/usr/bin/pbcopy")
  (literal "/usr/bin/pbpaste") (literal "/usr/bin/security") (literal "/usr/bin/lsappinfo") (literal "/usr/bin/say") (literal "/usr/sbin/screencapture"))
(deny mach-lookup (global-name "com.apple.coreservices.launchservicesd") (global-name "com.apple.lsd.mapdb") (global-name "com.apple.lsd.modifydb")
  (global-name "com.apple.pasteboard.1") (global-name "com.apple.coreservices.appleevents") (global-name "com.apple.windowserver.active")
  (global-name "com.apple.dock.server") (global-name "com.apple.SecurityServer") (global-name "com.apple.securityd.xpc"))
"""


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def safe_filename(name: str) -> str | None:
    base = Path(str(name)).name
    return base if _SAFE_NAME.match(base) and base == str(name) else None


def _limits(cpu_s: int, max_file_mb: int):
    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_mb * 2**20, max_file_mb * 2**20))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        except (ValueError, OSError):
            pass
        os.nice(10)

    return apply


def _watch(proc: subprocess.Popen, result: RunResult, timeout_s: float, memory_mb: int, max_procs: int, done: threading.Event, marker: str = "") -> None:
    start = time.monotonic()
    try:
        root = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return
    while not done.is_set():
        if proc.poll() is not None:
            # The program finished; anything it left behind (even detached) goes now, so a
            # leftover holding the output pipe can't stall the run until the timeout.
            if marker:
                _kill_marked(marker)
            return
        try:
            tree = [root, *root.children(recursive=True)]
            rss = sum(x.memory_info().rss for x in tree if x.is_running())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            tree, rss = [], 0
        reason = None
        if time.monotonic() - start > timeout_s:
            reason = "timeout"
        elif rss > memory_mb * 2**20:
            reason = "memory"
        elif len(tree) > max_procs:
            reason = "processes"
        if reason:
            result.killed = reason
            result.timed_out = reason == "timeout"
            _kill_tree(proc)
            if marker:
                _kill_marked(marker)
            return
        done.wait(0.05)


def _kill_marked(marker: str) -> None:
    """Kill every process carrying this run's marker, including ones that detached
    from the process group (double fork + setsid)."""
    for p in psutil.process_iter(["pid"]):
        try:
            if p.environ().get("TUTOR_SANDBOX_RUN") == marker:
                p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        for c in psutil.Process(proc.pid).children(recursive=True):
            c.kill()
        proc.kill()
    except (psutil.NoSuchProcess, ProcessLookupError):
        pass


def _docker_up() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --- running --------------------------------------------------------------------------------------------


def run(
    files: dict[str, str],
    language: str | None = None,
    *,
    main: str | None = None,
    stdin: str = "",
    tests: list[dict] | None = None,
    limits: dict | None = None,
    p: Paths | None = None,
    backend: str = "subprocess",
) -> RunResult:
    """Run `files` (name → content). `main` is the file to run (default: the first).
    tests: [{"name", "stdin", "expected"}] (compares stdout) or [{"name", "file", "content"}]
    (an extra file, e.g. a test script, run instead of main; passes if exit code is 0)."""
    p = p or paths()
    lim = {**DEFAULTS, **(limits or {})}
    langs = L.load(p)
    main = main or next(iter(files))
    language = language or L.for_filename(main, langs)
    if not language or language not in langs:
        return RunResult(language=language or "unknown", stderr=f"Don't know how to run {main}. Add the language to languages.yaml.", exit_code=-1)
    spec = langs[language]
    mode = spec.get("mode", "run")
    if mode == "run" and tests:
        results = []
        base = None
        for t in tests:
            if "file" in t:
                r = _run_once({**files, t["file"]: t["content"]}, language, spec, t["file"], "", lim, p, backend)
                passed = r.exit_code == 0 and not r.killed
                results.append({"name": t.get("name", t["file"]), "passed": passed, "expected": "exit 0", "actual": f"exit {r.exit_code}", "stderr": _clip(r.stderr, 600)})
            else:
                r = _run_once(files, language, spec, main, t.get("stdin", ""), lim, p, backend)
                actual = r.stdout.strip()
                passed = actual == str(t.get("expected", "")).strip() and not r.killed
                results.append({"name": t.get("name", f"test {len(results) + 1}"), "passed": passed, "expected": t.get("expected"), "actual": _clip(actual, 600)})
            base = base or r
            if r.killed:
                base = r
        base = base or RunResult(language=language)
        base.tests = results
        return base
    return _run_once(files, language, spec, main, stdin, lim, p, backend)


def _run_once(files, language, spec, main, stdin, lim, p: Paths, backend: str) -> RunResult:
    mode = spec.get("mode", "run")
    python = sandbox_python(p) if "{python}" in (spec.get("run", "") + str(spec.get("requires", ""))) else ""
    cache = p.cache / "sandbox"  # resolved below: the sandbox matches real paths (/var → /private/var)
    cache.mkdir(parents=True, exist_ok=True)
    cache = cache.resolve()
    if not L.requirement_ok(spec, python or sandbox_python(p, create=False)):
        return RunResult(language=language, stderr=f"{language}: toolchain not installed ({spec.get('requires')}).", exit_code=-1, mode=mode)
    names = {name: safe_filename(name) for name in files}
    if not all(names.values()) or not safe_filename(main):
        return RunResult(language=language, stderr="File names may only use letters, digits, '.', '_' and '-'.", exit_code=-1, mode=mode)
    with tempfile.TemporaryDirectory(prefix="tutor-run-") as tmp:
        work = Path(tmp).resolve()
        for name, content in files.items():
            (work / names[name]).write_text(content)
        cmd_t = spec["check" if mode == "check" else "run"]
        q = shlex.quote
        cmd = cmd_t.format(file=q(Path(main).name), dir=q(str(work)), python=q(python) if python else "", stem=q(Path(main).stem), cache=q(str(cache)))
        if backend == "docker" and language == "python" and _docker_up():
            return _run_docker(work, Path(main).name, stdin, lim, language)
        extra_read = [str(Path(python).parent.parent) if python else "", str(Path.home() / ".local/share/uv/python"), str(cache)]
        if spec.get("allow_network") and mode == "check":
            extra_read.append(str(Path.home() / "Library/Caches"))
        # Only trusted config checkers (mode: check) may opt into the network, e.g. kubeconform fetching schemas.
        network = bool(spec.get("allow_network")) and mode == "check"
        profile = _profile(work, [x for x in extra_read if x], network, [str(cache)] if network else None, allow_fork=bool(spec.get("allow_fork")))
        argv = ["sandbox-exec", "-p", profile, "/bin/sh", "-c", cmd]
        marker = uuid.uuid4().hex
        env = {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(work), "TMPDIR": str(work), "LANG": "en_US.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "JAVA_TOOL_OPTIONS": f"-Xss8m -Djava.io.tmpdir={work}", "TUTOR_SANDBOX_RUN": marker}
        result = RunResult(language=language, mode=mode)
        started = time.monotonic()
        proc = subprocess.Popen(argv, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                preexec_fn=_limits(lim["cpu_s"], lim["max_file_mb"]))
        done = threading.Event()
        watcher = threading.Thread(target=_watch, args=(proc, result, lim["timeout_s"], lim["memory_mb"], lim["max_procs"], done, marker), daemon=True)
        watcher.start()
        try:
            out, err = proc.communicate(stdin.encode(), timeout=lim["timeout_s"] + 5)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            _kill_marked(marker)  # a detached child may be holding the output pipe open
            out, err = proc.communicate()
            result.killed = result.killed or "timeout"
        done.set()
        watcher.join(1)
        _kill_marked(marker)  # nothing a run started outlives it
        result.duration_s = round(time.monotonic() - started, 3)
        result.exit_code = proc.returncode
        if proc.returncode in (-24, 152) and not result.killed:  # SIGXCPU
            result.killed = "cpu"
        result.stdout = _clip(out.decode(errors="replace"), lim["max_output"]).replace(str(work) + "/", "")
        result.stderr = _clip(err.decode(errors="replace"), lim["max_output"]).replace(str(work) + "/", "")
        return result


def _run_docker(work: Path, main: str, stdin: str, lim: dict, language: str) -> RunResult:
    argv = ["docker", "run", "--rm", "-i", "--network", "none", "--memory", f"{lim['memory_mb']}m", "--pids-limit", str(lim["max_procs"]),
            "--cpus", "1", "-v", f"{work}:/work", "-w", "/work", "python:3.12-slim", "python", main]
    started = time.monotonic()
    try:
        r = subprocess.run(argv, input=stdin.encode(), capture_output=True, timeout=lim["timeout_s"] + 10)
        return RunResult(language=language, stdout=r.stdout.decode(errors="replace"), stderr=r.stderr.decode(errors="replace"), exit_code=r.returncode,
                         duration_s=round(time.monotonic() - started, 3), backend="docker")
    except subprocess.TimeoutExpired:
        return RunResult(language=language, exit_code=-9, killed="timeout", timed_out=True, backend="docker")


def check_languages(p: Paths | None = None) -> list[dict]:
    """`tutor languages check`: each language's toolchain plus a hello-world run."""
    p = p or paths()
    out = []
    for name, spec in L.load(p).items():
        hello = spec.get("hello")
        filename = spec.get("filename") or f"main{spec.get('extensions', ['.txt'])[0]}"
        if not hello:
            out.append({"language": name, "ok": True, "detail": "no hello program configured"})
            continue
        r = run({filename: hello}, name, p=p)
        if spec.get("mode") == "check":
            ok = r.exit_code == 0
            detail = "checker works" if ok else (r.stderr or r.stdout).strip()[:160]
        else:
            ok = r.exit_code == 0 and spec.get("expect", "") in r.stdout
            detail = f"ran in {r.duration_s:.2f}s" if ok else (r.stderr or r.stdout or f"exit {r.exit_code}").strip()[:160]
        out.append({"language": name, "ok": ok, "detail": detail})
    return out
