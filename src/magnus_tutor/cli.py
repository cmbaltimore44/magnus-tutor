"""`tutor` command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import webbrowser

from . import courses as C
from . import doctor, hardware, runtime
from .config import load_settings, paths
from .setup import bootstrap


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


# --- process commands -------------------------------------------------------------


def cmd_init(a) -> int:
    r = bootstrap()
    for c in r["created"]:
        print(f"created {c}")
    if r["prompts_installed"]:
        print("installed prompts: " + ", ".join(r["prompts_installed"]))
    if r["recommendation"]:
        rec = r["recommendation"]
        print(f"preset: {rec['preset']} (model budget {rec['budget_gb']} GB)")
    print(f"config: {paths().config}")
    return 0


def cmd_start(a) -> int:
    bootstrap()
    r = runtime.start_server()
    if not r.get("running"):
        print(f"tutor: {r.get('error')}", file=sys.stderr)
        return 1
    url = r["url"] + (a.path or "")
    print(f"Magnus Tutor {'is running' if r.get('already') else 'started'} at {url}")
    if not a.no_open:
        webbrowser.open(url)
    return 0


def cmd_stop(a) -> int:
    r = runtime.stop_all()
    print("backend: " + ("stopped" if r["server"] else "wasn't running"))
    print("models unloaded: " + (", ".join(r["unloaded"]) or "none loaded"))
    print("ollama: " + ("stopped (it was started by the tutor)" if r["ollama"] else "not started by the tutor; left alone"))
    return 0


def cmd_status(a) -> int:
    s = load_settings()
    up = runtime.server_up(s)
    print(f"backend: {'running at ' + runtime.server_url(s) if up else 'stopped'}")
    print(f"ollama:  {'running' if runtime.ollama_up(s['ollama']['host']) else 'stopped'}")
    return 0


def cmd_serve(a) -> int:
    bootstrap()
    from .server.__main__ import main

    main()
    return 0


def cmd_doctor(a) -> int:
    r = doctor.report()
    if a.json:
        _print(r)
    else:
        print(doctor.format_report(r))
    return 0


def cmd_hardware(a) -> int:
    hw = hardware.detect()
    rec = hardware.recommend(hw)
    if a.json:
        _print({"hardware": hardware.as_dict(hw), "recommendation": rec})
        return 0
    print(f"Chip: {hw.chip} · RAM {hw.ram_gb} GB · {hw.available_gb} GB available" + (" · on battery" if hw.on_battery else ""))
    print(f"Preset: {rec['preset']} · model budget ≤ {rec['budget_gb']} GB (half of RAM)")
    for role, m in rec["models"].items():
        print(f"  {role:<9} {m}")
    print("Candidates that fit: " + ", ".join(rec["candidates"]))
    if rec["experimental"]:
        print("Experimental (opt-in, memory pressure likely): " + ", ".join(rec["experimental"]))
    for n in rec["notes"]:
        print(f"Note: {n}")
    return 0


# --- chat ------------------------------------------------------------------------------


def cmd_chat(a) -> int:
    bootstrap()
    from .context import system_prompt
    from .llm.manager import ModelManager

    p = paths()
    settings = load_settings(p)
    o = runtime.ensure_ollama(settings, p)
    if not o["running"]:
        print(f"tutor: {o.get('error')}", file=sys.stderr)
        return 1
    course = C.load_course(a.course, p) if a.course else None
    if a.course and not course:
        print(f"tutor: no course '{a.course}' (see `tutor course list`)", file=sys.stderr)
        return 1
    models = ModelManager(settings)
    history = [{"role": "system", "content": system_prompt("ask", course, p=p)}]
    one_shot = " ".join(a.question) if a.question else None
    if not one_shot:
        print(f"Chatting about {course.name if course else 'anything'} with {models.model_for('tutor')}. Ctrl-D to quit.")

    async def turn(text: str) -> None:
        history.append({"role": "user", "content": text})
        out = []
        async for c in models.chat("tutor", history):
            if c.text:
                out.append(c.text)
                print(c.text, end="", flush=True)
            if c.done and a.stats:
                print(f"\n[{json.dumps(c.stats)}]", end="")
        print()
        history.append({"role": "assistant", "content": "".join(out)})

    if one_shot:
        asyncio.run(turn(one_shot))
        return 0
    while True:
        try:
            text = input("\nyou › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text:
            print("tutor › ", end="", flush=True)
            asyncio.run(turn(text))


# --- courses ---------------------------------------------------------------------------


def cmd_course(a) -> int:
    p = paths()
    bootstrap(p)
    if a.action == "list":
        for c in C.list_courses(p, include_archived=a.all):
            print(f"{c.slug:<14} {c.status:<9} {c.term:<10} {c.title}")
        return 0
    if a.action == "add":
        if not a.title:
            print("tutor course add: --title is required", file=sys.stderr)
            return 1
        c = C.create_course(a.title, code=a.code or "", short=a.short or "", term=a.term or "", instructor=a.instructor or "",
                            kind=a.kind or [], languages=a.languages or [], p=p)
        print(f"added {c.slug}: {p.courses / c.slug / 'course.yaml'}")
        for k, v in c.folders.items():
            print(f"  {k:<9} {v}")
        return 0
    if a.action == "archive":
        C.set_status(a.slug, "archived", p)
        print(f"archived {a.slug}")
        return 0
    if a.action == "unarchive":
        C.set_status(a.slug, "active", p)
        print(f"{a.slug} is active again")
        return 0
    if a.action == "archive-term":
        print("archived: " + (", ".join(C.archive_term(a.term, p)) or "nothing"))
        return 0
    return 1


def cmd_models(a) -> int:
    from .models_cmd import run

    return run(a)


def cmd_languages(a) -> int:
    from .sandbox import check_languages

    ok = True
    for r in check_languages():
        mark = "✓" if r["ok"] else "✗"
        ok &= r["ok"]
        print(f"{mark} {r['language']:<12} {r['detail']}")
    return 0 if ok else 1


def cmd_ingest(a) -> int:
    from .ingest.cli import run

    return run(a)


def cmd_bench(a) -> int:
    from .bench import run

    return run(a)


def cmd_prompts(a) -> int:
    from . import prompts

    p = paths()
    bootstrap(p)
    if a.action == "list":
        course = C.load_course(a.course, p) if a.course else None
        for n in prompts.NAMES:
            _, where = prompts.source(n, course, p)
            print(f"{n:<20} {where:<14} {prompts.DESCRIPTIONS[n]}")
        print("\nVariables: " + ", ".join("{{" + v + "}}" for v in prompts.VARIABLES))
        return 0
    if a.action == "edit":
        import os
        import subprocess

        f = (p.courses / a.course / "prompts" / f"{a.name}.md") if a.course else (p.prompts / f"{a.name}.md")
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(prompts.source(a.name, C.load_course(a.course, p) if a.course else None, p)[0])
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "fresh"
        before = f.read_text()
        subprocess.run([editor, str(f)])
        after = f.read_text()
        if after != before:
            from .db import get_db

            db = get_db(p)
            db.insert("prompt_versions", name=a.name, course=a.course, content=before, note="before $EDITOR edit", created_at=__import__("time").time())
            db.insert("prompt_versions", name=a.name, course=a.course, content=after, note="edited with $EDITOR", created_at=__import__("time").time())
            print(f"saved {f} (takes effect on the next turn)")
        return 0
    if a.action == "reset":
        from .db import get_db

        prompts.reset(get_db(p), a.name, a.course, p)
        print(f"reset {a.name}")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tutor", description="Magnus Tutor: a local, Socratic study tutor.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create config folders, prompts, profile template").set_defaults(fn=cmd_init)
    s = sub.add_parser("start", help="start the backend (if needed) and open the web app")
    s.add_argument("--no-open", action="store_true")
    s.add_argument("--path", default="", help="open this path, e.g. /session/12")
    s.set_defaults(fn=cmd_start)
    sub.add_parser("stop", help="stop the backend, unload models, stop the Ollama it started").set_defaults(fn=cmd_stop)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("serve", help="run the backend in the foreground").set_defaults(fn=cmd_serve)
    s = sub.add_parser("doctor", help="what's running, loaded, and on disk")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("hardware", help="hardware check and model recommendations")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_hardware)

    s = sub.add_parser("chat", help="chat in the terminal with your profile and course as context")
    s.add_argument("--course", "-c")
    s.add_argument("--stats", action="store_true", help="print latency and tokens/sec")
    s.add_argument("question", nargs="*")
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("course", help="list, add, archive courses")
    s.add_argument("action", choices=["list", "add", "archive", "unarchive", "archive-term"])
    s.add_argument("slug", nargs="?")
    s.add_argument("--title")
    s.add_argument("--code")
    s.add_argument("--short")
    s.add_argument("--term")
    s.add_argument("--instructor")
    s.add_argument("--kind", nargs="*", choices=list(C.KINDS))
    s.add_argument("--languages", nargs="*")
    s.add_argument("--all", action="store_true", help="include archived")
    s.set_defaults(fn=cmd_course)

    s = sub.add_parser("prompts", help="list, edit ($EDITOR) or reset prompts")
    s.add_argument("action", choices=["list", "edit", "reset"])
    s.add_argument("name", nargs="?")
    s.add_argument("--course")
    s.set_defaults(fn=cmd_prompts)

    s = sub.add_parser("models", help="installed models, pull (asks first), remove, unload")
    s.add_argument("action", choices=["list", "pull", "rm", "unload", "loaded"])
    s.add_argument("name", nargs="?")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_models)

    s = sub.add_parser("languages", help="check the code sandbox languages")
    s.add_argument("action", choices=["check"], nargs="?", default="check")
    s.set_defaults(fn=cmd_languages)

    s = sub.add_parser("ingest", help="ingest course materials (notes, textbooks)")
    s.add_argument("course", nargs="?")
    s.add_argument("--file")
    s.add_argument("--yes", action="store_true", help="confirm large vision jobs")
    s.add_argument("--status", action="store_true")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("bench", help="benchmark models on the eval set")
    s.add_argument("action", choices=["run", "report", "latency", "concurrency", "retrieval"], nargs="?", default="run")
    s.add_argument("--models", nargs="*")
    s.add_argument("--roles", nargs="*")
    s.add_argument("--limit", type=int)
    s.add_argument("--samples", type=int, default=1)
    s.set_defaults(fn=cmd_bench)

    a = ap.parse_args(argv)
    if a.cmd == "prompts" and a.action != "list" and not a.name:
        ap.error("prompts edit/reset need a prompt name")
    if a.cmd == "course" and a.action in ("archive", "unarchive") and not a.slug:
        ap.error("course archive needs a slug")
    if a.cmd == "course" and a.action == "archive-term" and not a.term:
        ap.error("course archive-term needs --term")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
