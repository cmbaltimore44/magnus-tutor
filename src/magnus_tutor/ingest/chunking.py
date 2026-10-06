"""Section-aware chunking. Textbooks: chunks never cross a section boundary and
remember their heading path and pages. Notes: one or two chunks per page, with
the page image kept, since handwriting transcriptions need checking."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .pdf import PageInfo

TARGET = 1100  # characters per chunk (≈250-300 tokens): small enough for precise citations
OVERLAP = 150


@dataclass
class Chunk:
    text: str
    page_index: int
    page_end: int
    printed_page: str
    section_path: str
    section_number: str


def _paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for p in paras:
        if len(p) <= TARGET * 1.5:
            out.append(p)
        else:  # very long paragraph: split on sentences
            sent, buf = re.split(r"(?<=[.!?])\s+", p), ""
            for s in sent:
                if len(buf) + len(s) > TARGET and buf:
                    out.append(buf.strip())
                    buf = ""
                buf += s + " "
            if buf.strip():
                out.append(buf.strip())
    return out


def _strip_running_heads(text: str) -> str:
    # pymupdf4llm keeps running headers like "**6 • Chapter Review 273**"; they add noise.
    lines = text.splitlines()
    keep = [ln for ln in lines[:3] if not re.fullmatch(r"\**\s*[\d.]+\s*•.*?\d+\s*\**|\**\s*\d+\s*\**", ln.strip())] + lines[3:]
    return "\n".join(keep)


def chunk_textbook(pages: list[PageInfo]) -> list[Chunk]:
    chunks: list[Chunk] = []
    groups: list[tuple[str, list[PageInfo]]] = []
    for pg in pages:
        if not pg.text:
            continue
        key = pg.section.path if pg.section else ""
        if groups and groups[-1][0] == key:
            groups[-1][1].append(pg)
        else:
            groups.append((key, [pg]))
    for path, pgs in groups:
        number = pgs[0].section.number if pgs[0].section else ""
        buf, start = "", pgs[0]
        last = pgs[0]
        for pg in pgs:
            for para in _paragraphs(_strip_running_heads(pg.text)):
                if buf and len(buf) + len(para) > TARGET:
                    chunks.append(Chunk(buf.strip(), start.index, last.index, start.printed, path, number))
                    tail = buf[-OVERLAP:]
                    buf, start = tail[tail.find(" ") + 1 :] + "\n\n", pg
                if not buf.strip():
                    start = pg
                buf += para + "\n\n"
                last = pg
        if buf.strip():
            chunks.append(Chunk(buf.strip(), start.index, last.index, start.printed, path, number))
    return chunks


def chunk_notes(pages: list[PageInfo]) -> list[Chunk]:
    chunks = []
    for pg in pages:
        if not pg.text:
            continue
        parts = _paragraphs(pg.text)
        buf = ""
        for para in parts:
            if buf and len(buf) + len(para) > TARGET * 1.4:
                chunks.append(Chunk(buf.strip(), pg.index, pg.index, pg.printed, pg.section.path if pg.section else "", ""))
                buf = ""
            buf += para + "\n\n"
        if buf.strip():
            chunks.append(Chunk(buf.strip(), pg.index, pg.index, pg.printed, pg.section.path if pg.section else "", ""))
    return chunks
