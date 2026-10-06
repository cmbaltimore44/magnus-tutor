"""Numbered exercises at the end of chapters, so "chapter 6, problem 12" finds the
exact problem. Exercise zones start at a heading like Problems / Exercises /
Conceptual Questions and last until the next chapter."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .pdf import PageInfo

_ZONE = re.compile(
    r"(?im)^\s*\**\s*(problems|exercises|conceptual questions|additional problems|challenge problems|review questions|homework problems|questions|end[- ]of[- ]chapter problems)\s*\**\s*$"
)
_ITEM = re.compile(r"(?m)^\s*(?:-\s*)?\**\s*(\d{1,3})\s*\**\s*\.\s+")


@dataclass
class Exercise:
    chapter: str
    number: str
    page_index: int
    printed_page: str
    text: str
    kind: str  # problems | conceptual questions | ...


def find_exercises(pages: list[PageInfo]) -> list[Exercise]:
    out: list[Exercise] = []
    zone_chapter, zone_kind = None, None
    pending: Exercise | None = None
    for pg in pages:
        plain = pg.extra.get("plain") or pg.text
        chapter = pg.section.chapter if pg.section else ""
        if zone_chapter is not None and chapter and chapter != zone_chapter:
            zone_chapter = None
            if pending:
                out.append(pending)
                pending = None
        m = _ZONE.search(plain)
        if m:
            zone_chapter, zone_kind = chapter, m.group(1).lower()
            plain = plain[m.end() :]
        if zone_chapter is None:
            continue
        # Kind changes inside the page (e.g. "Problems" after "Conceptual Questions").
        segments = []
        last = 0
        for mm in _ZONE.finditer(plain):
            segments.append((zone_kind, plain[last : mm.start()]))
            zone_kind = mm.group(1).lower()
            last = mm.end()
        segments.append((zone_kind, plain[last:]))
        for kind, seg in segments:
            items = list(_ITEM.finditer(seg))
            if pending and items:
                pending.text += " " + seg[: items[0].start()]
            elif pending and not items:
                pending.text += " " + seg
                continue
            for i, it in enumerate(items):
                if pending:
                    out.append(pending)
                end = items[i + 1].start() if i + 1 < len(items) else len(seg)
                pending = Exercise(zone_chapter, it.group(1), pg.index, pg.printed, seg[it.end() : end], kind)
    if pending:
        out.append(pending)
    for e in out:
        e.text = re.sub(r"\s+", " ", e.text).strip()[:3000]
    # Keep the first occurrence per (chapter, kind, number).
    seen, uniq = set(), []
    for e in out:
        key = (e.chapter, e.kind, e.number)
        if key not in seen and len(e.text) > 15:
            seen.add(key)
            uniq.append(e)
    return uniq


_LOOKUP = [
    re.compile(r"\b(?:ch(?:apter)?\.?\s*)(\d+)\s*[,:;]?\s*(?:problem|exercise|question|prob\.?|ex\.?|#|no\.?)\s*#?\s*(\d+)\b", re.I),
    re.compile(r"\b(?:problem|exercise|question|prob\.?|ex\.?)\s*#?\s*(\d+)\s*(?:in|from|of)\s*ch(?:apter)?\.?\s*(\d+)\b", re.I),
    re.compile(r"\b(?:problem|exercise|prob\.?|ex\.?)\s*(\d+)\.(\d+)\b", re.I),
]


def parse_lookup(text: str) -> tuple[str, str] | None:
    """('6', '12') from "chapter 6, problem 12", "problem 12 in chapter 6", "problem 6.12"."""
    for i, rx in enumerate(_LOOKUP):
        m = rx.search(text)
        if m:
            a, b = m.group(1), m.group(2)
            return (b, a) if i == 1 else (a, b)
    return None
