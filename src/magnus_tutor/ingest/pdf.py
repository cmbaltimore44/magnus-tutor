"""PDF reading: per-page text (Markdown via pymupdf4llm), section tree from the
bookmarks, printed page numbers, equation-gap detection, page rendering.

Equation-heavy textbooks often draw math as glyphs with no Unicode mapping, so
extracted text says "a planar surface of area  that is perpendicular". Those
pages are flagged (`gaps`) and repaired on demand with the vision model.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

MIN_TEXT_CHARS = 80  # below this a page counts as "no usable text" (handwriting, scans)


@dataclass
class Section:
    level: int
    title: str
    start: int  # 0-based page index
    end: int  # exclusive
    path: str = ""
    number: str = ""  # "6.3" or "6"
    chapter: str = ""


@dataclass
class PageInfo:
    index: int
    printed: str
    text: str
    has_text: bool
    gaps: bool
    section: Section | None = None
    extra: dict = field(default_factory=dict)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def open_pdf(path: Path):
    return pymupdf.open(path)


def title_of(doc, path: Path) -> str:
    t = (doc.metadata or {}).get("title") or ""
    t = t.strip()
    if not t or len(t) < 3 or t.lower() in ("untitled", "microsoft word"):
        t = path.stem.replace("_", " ").replace("-", " ")
    return t[:120]


_SEC_NUM = re.compile(r"^\s*(?:chapter\s+|ch\.?\s*|§\s*)?(\d+(?:\.\d+)*)\b", re.I)


def sections(doc) -> list[Section]:
    """The bookmark tree as flat sections with page ranges and heading paths."""
    raw = doc.get_toc(simple=True)
    n = doc.page_count
    # Drop out-of-order bookmarks (e.g. a stray "Blank Page" pointing back to page 2 at the end).
    toc, high = [], 0
    for level, title, page in raw:
        if page < high - 1 or page < 1:
            continue
        high = max(high, page)
        toc.append((level, title, page))
    out: list[Section] = []
    stack: list[Section] = []
    for i, (level, title, page) in enumerate(toc):
        start = max(0, min(n - 1, page - 1))
        end = n
        for lvl2, _, page2 in toc[i + 1 :]:
            if lvl2 <= level:
                end = max(start + 1, min(n, page2 - 1))
                break
        title = re.sub(r"\s+", " ", title).strip()
        sec = Section(level=level, title=title, start=start, end=end)
        while stack and stack[-1].level >= level:
            stack.pop()
        sec.path = " > ".join([s.title for s in stack] + [title])
        m = _SEC_NUM.match(title)
        sec.number = m.group(1) if m else ""
        chap_src = next((s for s in stack if s.level == 1), sec if level == 1 else None)
        cm = _SEC_NUM.match(chap_src.title) if chap_src else None
        sec.chapter = cm.group(1).split(".")[0] if cm else (sec.number.split(".")[0] if sec.number else "")
        stack.append(sec)
        out.append(sec)
    return out


def deepest_section(secs: list[Section], page: int) -> Section | None:
    best = None
    for s in secs:
        if s.start <= page < s.end and (best is None or s.level >= best.level):
            best = s
    return best


def infer_printed_pages(doc, texts: list[str]) -> list[str]:
    """PDF page labels if present; otherwise the offset implied by numbers in headers/footers."""
    labels = [doc[i].get_label() or "" for i in range(doc.page_count)]
    if sum(1 for x in labels if x) > doc.page_count // 2:
        return labels
    offsets = Counter()
    for i, t in enumerate(texts):
        lines = [ln.strip("* ").strip() for ln in t.strip().splitlines() if ln.strip()]
        edge = lines[:3] + lines[-3:]
        for ln in edge:
            for m in re.finditer(r"(?<![\d.])(\d{1,4})(?![\d.])", ln):
                v = int(m.group(1))
                if 0 < v <= doc.page_count + 50:
                    offsets[v - i] += 1
    if not offsets:
        return [str(i + 1) for i in range(doc.page_count)]
    off, count = offsets.most_common(1)[0]
    if count < max(3, doc.page_count // 10):
        return [str(i + 1) for i in range(doc.page_count)]
    return [str(i + off) if i + off > 0 else "" for i in range(doc.page_count)]


def has_gaps(plain: str) -> bool:
    """Heuristic: equations dropped out of the text layer."""
    lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
    if len(lines) < 8:
        return False
    tiny = sum(1 for ln in lines if len(ln.split()) <= 3 and not re.match(r"^(figure|table|\d+(\.\d+)*)\b", ln, re.I))
    dangling = len(re.findall(r"\b(of|is|by|the|and|to|that|as|where|gives|equals)\n", plain))
    lone = len(re.findall(r"(?m)^[()=+\-−,.]\s*$", plain))
    return tiny / len(lines) > 0.28 or dangling >= 4 or lone >= 3


def extract_pages(path: Path, progress=None, markdown: bool = True) -> tuple[list[PageInfo], list[Section], str]:
    """Text for every page. Markdown (headings, joined paragraphs) when possible."""
    with open_pdf(path) as doc:
        return _extract(doc, path, progress, markdown)


def _extract(doc, path: Path, progress, markdown: bool):
    plain = [doc[i].get_text() for i in range(doc.page_count)]
    md = None
    if markdown:
        try:
            import pymupdf4llm

            md = []
            step = 25
            for start in range(0, doc.page_count, step):
                pages = list(range(start, min(doc.page_count, start + step)))
                chunks = pymupdf4llm.to_markdown(doc, pages=pages, page_chunks=True, show_progress=False)
                md.extend(c["text"] for c in chunks)
                if progress:
                    progress(min(1.0, (start + step) / doc.page_count))
            if len(md) != doc.page_count:
                md = None
        except Exception:
            md = None
    secs = sections(doc)
    printed = infer_printed_pages(doc, plain)
    pages = []
    for i in range(doc.page_count):
        text = (md[i] if md else plain[i]).strip()
        usable = len(re.sub(r"\s+", "", plain[i])) >= MIN_TEXT_CHARS
        pages.append(PageInfo(index=i, printed=printed[i], text=text if usable else "", has_text=usable, gaps=usable and has_gaps(plain[i]),
                              section=deepest_section(secs, i), extra={"plain": plain[i]}))
    return pages, secs, title_of(doc, path)


def render_png(path: Path, index: int, zoom: float = 1.6, highlight: str | None = None) -> bytes:
    with open_pdf(path) as doc:
        return _render(doc[index], zoom, highlight)


def _render(page, zoom: float, highlight: str | None) -> bytes:
    if highlight:
        needle = re.sub(r"\s+", " ", highlight).strip()[:80]
        rects = page.search_for(needle) if len(needle) > 12 else []
        if not rects:
            words = needle.split()
            for k in (8, 5):
                if len(words) >= k:
                    rects = page.search_for(" ".join(words[:k]))
                    if rects:
                        break
        for r in rects[:6]:
            annot = page.add_highlight_annot(r)
            annot.set_colors(stroke=(1.0, 0.85, 0.3))
            annot.update()
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def page_image_hash(path: Path, index: int) -> tuple[str, bytes]:
    """Rendered page image (for vision) and its hash (transcriptions are cached by it)."""
    png = render_png(path, index, zoom=1.4)
    return hashlib.sha256(png).hexdigest(), png


def page_count(path: Path) -> int:
    with open_pdf(path) as doc:
        return doc.page_count


def sections_of(path: Path) -> list[Section]:
    with open_pdf(path) as doc:
        return sections(doc)
