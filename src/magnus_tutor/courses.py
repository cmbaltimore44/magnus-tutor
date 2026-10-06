"""Student profile and courses: plain YAML files the user edits by hand.

profile.yaml               about the student
courses/<slug>/course.yaml one folder per course (status: active | archived)
courses/<slug>/prompts/    optional per-course prompt overrides
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Paths, load_yaml, paths, write_yaml_atomic

PROFILE_TEMPLATE = """\
# About you. The tutor reads this before every conversation.
# Fill in what you like; blank fields are skipped.
name:
school:
program:
year:
background: |
  # strengths, weak areas, prior courses
learning_preferences:
  # - likes intuition before formalism
  # - prefers worked examples with units carried through
resume: # optional path to a resume PDF, e.g. ~/.config/magnus-tutor/resume.pdf
"""

KINDS = ("math", "physics", "code", "writing", "general")


@dataclass
class Course:
    slug: str
    code: str = ""
    title: str = ""
    short: str = ""  # what labels and the timer show, e.g. "QM"
    instructor: str = ""
    term: str = ""
    status: str = "active"
    kind: list[str] = field(default_factory=list)  # math | physics | code | writing | general
    description: str = ""
    topics: list[str] = field(default_factory=list)
    notation_conventions: str = ""
    folders: dict[str, str] = field(default_factory=dict)
    languages: list[str] = field(default_factory=list)
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    schedule: str = ""
    grading: str = ""

    @property
    def name(self) -> str:
        return self.short or self.code or self.title or self.slug

    @property
    def is_writing(self) -> bool:
        return "writing" in self.kind and not ({"math", "physics", "code"} & set(self.kind))

    def folder(self, key: str) -> Path | None:
        v = self.folders.get(key)
        return Path(v).expanduser() if v else None

    def to_yaml(self) -> dict[str, Any]:
        d = {
            "code": self.code,
            "title": self.title,
            "short": self.short,
            "instructor": self.instructor,
            "term": self.term,
            "status": self.status,
            "kind": self.kind,
            "description": self.description,
            "topics": self.topics,
            "notation_conventions": self.notation_conventions,
            "schedule": self.schedule,
            "grading": self.grading,
            "folders": self.folders,
            "languages": self.languages,
            "prompt_overrides": self.prompt_overrides,
        }
        return d

    def summary(self) -> str:
        """Plain-text course description for prompts."""
        lines = [f"{self.title}" + (f" ({self.code})" if self.code else "")]
        if self.term:
            lines.append(f"Term: {self.term}")
        if self.instructor:
            lines.append(f"Instructor: {self.instructor}")
        if self.kind:
            lines.append(f"Kind: {', '.join(self.kind)}")
        if self.description:
            lines.append(f"Description: {self.description.strip()}")
        if self.topics:
            lines.append("Topics: " + "; ".join(self.topics))
        if self.languages:
            lines.append("Languages: " + ", ".join(self.languages))
        if self.notation_conventions:
            lines.append(f"Notation conventions: {self.notation_conventions.strip()}")
        return "\n".join(lines)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", "")).strip("-")
    return s[:40] or "course"


def load_course(slug: str, p: Paths | None = None) -> Course | None:
    p = p or paths()
    data = load_yaml(p.courses / slug / "course.yaml")
    if not isinstance(data, dict):
        return None
    known = {k: v for k, v in data.items() if k in Course.__dataclass_fields__ and k != "slug"}
    for k in ("kind", "topics", "languages"):
        if isinstance(known.get(k), str):
            known[k] = [known[k]]
        elif known.get(k) is None:
            known.pop(k, None)
    for k in ("folders", "prompt_overrides"):
        if not isinstance(known.get(k), dict):
            known.pop(k, None)
    for k, v in list(known.items()):
        if v is None:
            known.pop(k)
    return Course(slug=slug, **known)


def list_courses(p: Paths | None = None, include_archived: bool = False) -> list[Course]:
    p = p or paths()
    if not p.courses.exists():
        return []
    out = []
    for d in sorted(p.courses.iterdir()):
        if (d / "course.yaml").exists():
            c = load_course(d.name, p)
            if c and (include_archived or c.status != "archived"):
                out.append(c)
    return out


def save_course(course: Course, p: Paths | None = None) -> None:
    p = p or paths()
    write_yaml_atomic(p.courses / course.slug / "course.yaml", course.to_yaml())


def default_folders(slug: str, p: Paths | None = None) -> dict[str, str]:
    p = p or paths()
    root = p.materials_root
    return {k: str(root / k / slug) for k in ("notes", "textbooks", "other")}


def create_course(title: str, *, code: str = "", short: str = "", term: str = "", instructor: str = "", kind: list[str] | None = None,
                  languages: list[str] | None = None, slug: str | None = None, make_folders: bool = True, p: Paths | None = None, **extra) -> Course:
    p = p or paths()
    slug = slug or slugify(short or code or title)
    base, n = slug, 2
    while (p.courses / slug).exists():
        slug, n = f"{base}-{n}", n + 1
    c = Course(slug=slug, code=code, title=title, short=short, term=term, instructor=instructor, kind=kind or [], languages=languages or [],
               folders=default_folders(slug, p), **extra)
    if make_folders:
        for v in c.folders.values():
            Path(v).mkdir(parents=True, exist_ok=True)
    (p.courses / slug / "prompts").mkdir(parents=True, exist_ok=True)
    save_course(c, p)
    return c


def set_status(slug: str, status: str, p: Paths | None = None) -> Course:
    c = load_course(slug, p)
    if not c:
        raise KeyError(slug)
    c.status = status
    save_course(c, p)
    return c


def archive_term(term: str, p: Paths | None = None) -> list[str]:
    """Archive every active course from `term` (the new-semester flow)."""
    done = []
    for c in list_courses(p):
        if c.term == term:
            set_status(c.slug, "archived", p)
            done.append(c.slug)
    return done


# --- profile -----------------------------------------------------------------


def load_profile(p: Paths | None = None) -> dict[str, Any]:
    p = p or paths()
    data = load_yaml(p.profile, {}) or {}
    return data if isinstance(data, dict) else {}


def profile_text(profile: dict[str, Any]) -> str:
    """Student profile as prompt text; blank fields are left out."""
    labels = [("name", "Name"), ("school", "School"), ("program", "Program"), ("year", "Year")]
    lines = [f"{label}: {profile[k]}" for k, label in labels if profile.get(k)]
    bg = profile.get("background")
    if isinstance(bg, str) and bg.strip() and not bg.strip().startswith("#"):
        lines.append(f"Background: {bg.strip()}")
    prefs = profile.get("learning_preferences")
    if isinstance(prefs, list) and prefs:
        lines.append("Learning preferences: " + "; ".join(str(x) for x in prefs if x))
    summary = profile.get("resume_summary")
    if summary:
        lines.append(f"From their resume: {summary}")
    return "\n".join(lines) if lines else "(No profile yet: the student hasn't filled in profile.yaml.)"
