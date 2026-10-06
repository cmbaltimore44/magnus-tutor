"""Prompts are plain Markdown files the student can edit at any time.

Lookup order (first found wins), re-read on every turn so edits apply on the
next message without a restart:
  1. ~/.config/magnus-tutor/courses/<slug>/prompts/<name>.md   per-course override
  2. a path in the course's `prompt_overrides: {<name>: path}`
  3. ~/.config/magnus-tutor/prompts/<name>.md                    global (installed on first run)
  4. the packaged default
Every save through the app is recorded in `prompt_versions`, so it can be restored.
"""

from __future__ import annotations

import os
import re
from importlib import resources
from pathlib import Path

from ..config import Paths, paths
from ..db import DB, now

NAMES = ["persona", "office_hours", "writing_coach", "ask", "quiz", "solver", "code_tutor", "office_hours_state"]

DESCRIPTIONS = {
    "persona": "Shared tone and ground rules, included in every conversation.",
    "office_hours": "The Socratic problem-set prompt (office hours mode).",
    "writing_coach": "Office hours for writing courses: coaching, never ghostwriting.",
    "ask": "General questions grounded in your notes and textbooks.",
    "quiz": "Quiz mode: questions from your materials, graded against sources.",
    "solver": "The hidden solver pass. Must ask for JSON with the keys the engine reads.",
    "code_tutor": "Extra guidance added to code turns.",
    "office_hours_state": "Per-turn engine state (hint level, attempt status, private reference).",
}

VARIABLES = {
    "student_profile": "Your profile.yaml as text.",
    "course": "The course name, e.g. 'Electricity and Magnetism (PHYS 3xx)'.",
    "course_details": "Full course description: term, topics, languages, notation.",
    "notation_conventions": "The course's notation conventions.",
    "retrieved_context": "Passages retrieved from your notes and textbooks, with citation labels.",
    "problem": "The current problem text.",
    "mode": "office_hours | ask | quiz | code | writing.",
    "hint_level": "The hint level allowed this turn (0-4).",
    "hint_level_name": "Name of that hint level (e.g. 'which concept applies').",
    "hint_instruction": "What the tutor may reveal at this hint level.",
    "attempt_status": "Whether you've shared an attempt yet, and how it's going.",
    "reference_solution": "The hidden solver's reference solution (private).",
    "solution_confidence": "verified | agreed | uncertain | pending.",
    "verification_note": "What the verification tools found.",
    "quiz_topic": "Optional topic for quiz mode (prefixed with ': ').",
    "date": "Today's date.",
}

_VAR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def default_text(name: str) -> str:
    return resources.files(__package__).joinpath("defaults", f"{name}.md").read_text()


def install_defaults(p: Paths | None = None) -> list[str]:
    """Copy packaged prompts into the config dir. Never overwrites edits."""
    p = p or paths()
    p.prompts.mkdir(parents=True, exist_ok=True)
    written = []
    for name in NAMES:
        f = p.prompts / f"{name}.md"
        if not f.exists():
            f.write_text(default_text(name))
            written.append(name)
    return written


def _course_override_path(name: str, course, p: Paths) -> Path | None:
    if course is None:
        return None
    f = p.courses / course.slug / "prompts" / f"{name}.md"
    if f.exists():
        return f
    custom = (course.prompt_overrides or {}).get(name)
    if custom:
        base = (p.courses / course.slug).resolve()
        cp = (base / Path(custom).expanduser()).resolve()
        # Overrides must live inside the course folder and be Markdown.
        if cp.suffix == ".md" and base in cp.parents and cp.exists():
            return cp
    return None


def source(name: str, course=None, p: Paths | None = None, overrides: dict | None = None) -> tuple[str, str]:
    """(text, where) for a prompt, following the lookup order. `overrides` (name → text)
    wins over everything; the prompt tester uses it for unsaved drafts."""
    if overrides and name in overrides:
        return overrides[name], "draft"
    p = p or paths()
    f = _course_override_path(name, course, p)
    if f:
        return f.read_text(), f"course:{course.slug}"
    g = p.prompts / f"{name}.md"
    if g.exists():
        return g.read_text(), "global"
    return default_text(name), "default"


def render(template: str, variables: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        v = variables.get(m.group(1))
        return "" if v is None else str(v)

    return _VAR.sub(sub, template).strip()


def used_variables(template: str) -> list[str]:
    return sorted(set(_VAR.findall(template)))


def get(name: str, variables: dict[str, str], course=None, p: Paths | None = None, overrides: dict | None = None) -> str:
    text, _ = source(name, course, p, overrides)
    return render(text, variables)


def save(db: DB, name: str, content: str, course_slug: str | None = None, note: str | None = None, p: Paths | None = None) -> int:
    if name not in NAMES:
        raise KeyError(name)
    _check_slug(course_slug)
    p = p or paths()
    target = (p.courses / course_slug / "prompts" / f"{name}.md") if course_slug else (p.prompts / f"{name}.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    _record_baseline(db, name, course_slug, target)
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(content)
    os.replace(tmp, target)
    return db.insert("prompt_versions", name=name, course=course_slug, content=content, note=note, created_at=now())


def _record_baseline(db: DB, name: str, course_slug: str | None, target: Path) -> None:
    """Before the first app save, remember what the file held (e.g. hand edits)."""
    seen = db.one("SELECT 1 FROM prompt_versions WHERE name = ? AND course IS ?", (name, course_slug))
    if not seen and target.exists():
        db.insert("prompt_versions", name=name, course=course_slug, content=target.read_text(), note="before first edit in the app", created_at=now())


def reset(db: DB, name: str, course_slug: str | None = None, p: Paths | None = None) -> None:
    """Global: back to the packaged default (recorded as a version). Course: remove the override."""
    if name not in NAMES:
        raise KeyError(name)
    _check_slug(course_slug)
    p = p or paths()
    if course_slug:
        f = p.courses / course_slug / "prompts" / f"{name}.md"
        if f.exists():
            _record_baseline(db, name, course_slug, f)
            db.insert("prompt_versions", name=name, course=course_slug, content=f.read_text(), note="override removed", created_at=now())
            f.unlink()
        return
    save(db, name, default_text(name), None, note="reset to default", p=p)


def _check_slug(course_slug: str | None) -> None:
    from ..courses import valid_slug

    if course_slug is not None and not valid_slug(course_slug):
        raise ValueError("bad course")


def history(db: DB, name: str, course_slug: str | None = None) -> list[dict]:
    return db.all(
        "SELECT id, name, course, note, created_at, length(content) AS chars FROM prompt_versions WHERE name = ? AND course IS ? ORDER BY id DESC",
        (name, course_slug),
    )


def restore(db: DB, version_id: int, p: Paths | None = None) -> int:
    v = db.one("SELECT * FROM prompt_versions WHERE id = ?", (version_id,))
    if not v:
        raise KeyError(version_id)
    return save(db, v["name"], v["content"], v["course"], note=f"restored version {version_id}", p=p)
