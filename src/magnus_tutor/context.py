"""Context assembly: profile + course + retrieved passages + engine state → system prompt."""

from __future__ import annotations

import datetime as dt

from . import prompts
from .config import Paths, paths
from .courses import Course, load_profile, profile_text


def base_variables(course: Course | None, p: Paths | None = None) -> dict[str, str]:
    p = p or paths()
    return {
        "student_profile": profile_text(load_profile(p)),
        "course": (f"{course.title}" + (f" ({course.code})" if course.code else "")) if course else "general studies",
        "course_details": course.summary() if course else "",
        "notation_conventions": course.notation_conventions if course else "",
        "date": dt.date.today().isoformat(),
    }


def format_sources(passages: list[dict]) -> str:
    """Retrieved passages as a numbered block with citation labels."""
    if not passages:
        return "(No matching passages in the student's notes or textbooks.)"
    out = []
    for i, ps in enumerate(passages, 1):
        out.append(f"[S{i}] {ps['label']}\n{ps['text'].strip()}")
    return "\n\n".join(out)


def system_prompt(
    mode: str,
    course: Course | None,
    *,
    passages: list[dict] | None = None,
    state_vars: dict[str, str] | None = None,
    code: bool = False,
    p: Paths | None = None,
    overrides: dict | None = None,
) -> str:
    p = p or paths()
    v = base_variables(course, p)
    v["retrieved_context"] = format_sources(passages or [])
    v["mode"] = mode
    v.update(state_vars or {})
    parts = [prompts.get("persona", v, course, p, overrides)]
    if mode in ("office_hours", "code"):
        name = "writing_coach" if course and course.is_writing else "office_hours"
        parts.append(prompts.get(name, v, course, p, overrides))
    elif mode == "quiz":
        parts.append(prompts.get("quiz", v, course, p, overrides))
    else:
        parts.append(prompts.get("ask", v, course, p, overrides))
    if code or mode == "code":
        parts.append(prompts.get("code_tutor", v, course, p, overrides))
    if course:
        parts.append("About the course:\n" + v["course_details"])
    if passages is not None:
        parts.append("Sources from the student's materials (cite by their labels):\n" + v["retrieved_context"])
    if state_vars and mode in ("office_hours", "code"):
        parts.append(prompts.get("office_hours_state", v, course, p, overrides))
    return "\n\n".join(x for x in parts if x)
