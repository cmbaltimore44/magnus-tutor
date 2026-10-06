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


def _extract_inprocess(path: Path, progress=None, markdown: bool = True) -> tuple[list[PageInfo], list[Section], str]:
    """Text for every page. Markdown (headings, joined paragraphs) when possible. Runs inside
    the sandboxed worker; the backend calls extract_pages()."""
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


# --- the backend side: everything below runs the parser in the sandboxed worker ----------------

import json as _json  # noqa: E402
import os as _os  # noqa: E402
import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402
import sys as _sys  # noqa: E402
import tempfile as _tempfile  # noqa: E402

_REPO_SRC = Path(__file__).resolve().parents[2]


class PdfWorkerError(RuntimeError):
    pass


def _profile(pdf_path: Path, outdir: Path) -> str:
    home = str(Path.home())
    py = Path(_sys.executable).resolve()
    # The PDF, the output folder, the tutor's code and venv, and the folder of Python interpreters
    # (the venv's python links through uv's version-alias folder). Nothing else in your home folder.
    reads = {str(pdf_path.resolve()), str(outdir), str(_REPO_SRC), str(Path(_sys.prefix)), str(Path(_sys.base_prefix).parent), str(py.parent.parent)}
    allow = "\n".join(f'(allow file-read* (subpath "{r}"))' for r in sorted(reads))
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{outdir}"))
(allow file-write* (literal "/dev/null"))
(deny file-read* (subpath "{home}"))
(deny file-read* (subpath "/Volumes"))
{allow}
(deny process-fork)
(deny process-exec (literal "/usr/bin/open") (literal "/usr/bin/osascript") (literal "/bin/launchctl"))
(deny mach-lookup (global-name "com.apple.coreservices.launchservicesd") (global-name "com.apple.pasteboard.1")
  (global-name "com.apple.coreservices.appleevents") (global-name "com.apple.SecurityServer"))
"""


def _run(cmd: str, pdf_path: Path, *args: str, timeout: float = 120, progress=None) -> Path:
    """Run the worker; returns its output folder (caller deletes it)."""
    outdir = Path(_tempfile.mkdtemp(prefix="tutor-pdf-")).resolve()
    argv = [_sys.executable, "-I", "-m", "magnus_tutor.ingest.pdfworker", cmd, str(pdf_path), str(outdir), *args]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(outdir), "TMPDIR": str(outdir), "PYTHONPATH": str(_REPO_SRC), "LANG": "en_US.UTF-8"}
    if _shutil.which("sandbox-exec") and not _os.environ.get("MAGNUS_TUTOR_PDF_UNSANDBOXED"):
        argv = ["sandbox-exec", "-p", _profile(pdf_path, outdir), *argv]

    def limits():
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (int(timeout), int(timeout) + 1))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        _os.nice(10)

    proc = _subprocess.Popen(argv, stdout=_subprocess.PIPE, stderr=_subprocess.PIPE, text=True, env=env, cwd=str(outdir), preexec_fn=limits)
    try:
        for line in proc.stdout:
            if progress and line.startswith("PROGRESS "):
                try:
                    progress(float(line.split()[1]))
                except ValueError:
                    pass
        proc.wait(timeout=timeout)
    except _subprocess.TimeoutExpired:
        proc.kill()
        _shutil.rmtree(outdir, ignore_errors=True)
        raise PdfWorkerError("reading the PDF took too long")
    if proc.returncode != 0:
        err = (proc.stderr.read() or "").strip().splitlines()[-1:] if proc.stderr else []
        _shutil.rmtree(outdir, ignore_errors=True)
        raise PdfWorkerError(f"couldn't read the PDF ({err[0][:200] if err else 'exit ' + str(proc.returncode)})")
    return outdir


def _sections_from(data: list[dict]) -> list[Section]:
    return [Section(**d) for d in data]


def extract_pages(path: Path, progress=None, markdown: bool = True) -> tuple[list[PageInfo], list[Section], str]:
    """Text for every page (in the sandboxed worker). Large books may take a minute or two."""
    out = _run("extract", path, "1" if markdown else "0", timeout=900, progress=progress)
    try:
        data = _json.loads((out / "result.json").read_text())
    finally:
        _shutil.rmtree(out, ignore_errors=True)
    secs = _sections_from(data["sections"])
    pages = [PageInfo(index=p["index"], printed=p["printed"], text=p["text"], has_text=p["has_text"], gaps=p["gaps"],
                      section=deepest_section(secs, p["index"]), extra={"plain": p["plain"]}) for p in data["pages"]]
    return pages, secs, data["title"]


def render_pages(path: Path, indices: list[int], zoom: float = 1.4, highlight: str | None = None) -> dict[int, bytes]:
    """PNG renders of several pages in one worker run. The highlight text is passed as a
    plain argument (an argv list, never a shell)."""
    if not indices:
        return {}
    args = [str(zoom), ",".join(str(int(i)) for i in indices)]
    if highlight:
        args.append(highlight[:400])
    out = _run("render", path, *args, timeout=60 + 2 * len(indices))
    try:
        return {i: (out / f"{i}.png").read_bytes() for i in indices if (out / f"{i}.png").exists()}
    finally:
        _shutil.rmtree(out, ignore_errors=True)


def render_png(path: Path, index: int, zoom: float = 1.6, highlight: str | None = None) -> bytes:
    png = render_pages(path, [index], zoom, highlight).get(index)
    if png is None:
        raise PdfWorkerError("couldn't render that page")
    return png


def page_image_hash(path: Path, index: int) -> tuple[str, bytes]:
    """Rendered page image (for vision) and its hash (transcriptions are cached by it)."""
    png = render_png(path, index, zoom=1.4)
    return hashlib.sha256(png).hexdigest(), png


def page_images(path: Path, indices: list[int]) -> dict[int, tuple[str, bytes]]:
    """Batch version of page_image_hash: one worker run for many pages."""
    out = {}
    for start in range(0, len(indices), 40):
        for i, png in render_pages(path, indices[start : start + 40], zoom=1.4).items():
            out[i] = (hashlib.sha256(png).hexdigest(), png)
    return out


def page_count(path: Path) -> int:
    out = _run("sections", path, timeout=60)
    try:
        return _json.loads((out / "result.json").read_text())["pages"]
    finally:
        _shutil.rmtree(out, ignore_errors=True)


def sections_of(path: Path) -> list[Section]:
    out = _run("sections", path, timeout=60)
    try:
        return _sections_from(_json.loads((out / "result.json").read_text())["sections"])
    finally:
        _shutil.rmtree(out, ignore_errors=True)


def text_of(path_or_bytes, name: str = "upload.pdf") -> str:
    """All text of a PDF given as a path or as uploaded bytes (syllabus, resume)."""
    tmp = None
    if isinstance(path_or_bytes, (bytes, bytearray)):
        fd, tmpname = _tempfile.mkstemp(prefix="tutor-upload-", suffix=".pdf")
        _os.write(fd, path_or_bytes)
        _os.close(fd)
        tmp = Path(tmpname)
        path = tmp
    else:
        path = Path(path_or_bytes)
    try:
        out = _run("text", path, timeout=60)
        try:
            return "\n".join(_json.loads((out / "result.json").read_text())["pages"])
        finally:
            _shutil.rmtree(out, ignore_errors=True)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def image_to_png(data: bytes) -> bytes | None:
    """Convert an uploaded image (HEIC, WebP, GIF...) to PNG in the worker."""
    fd, tmpname = _tempfile.mkstemp(prefix="tutor-upload-", suffix=".img")
    _os.write(fd, data)
    _os.close(fd)
    try:
        out = _run("render", Path(tmpname), "1.0", "0", timeout=30)
        try:
            f = out / "0.png"
            return f.read_bytes() if f.exists() else None
        finally:
            _shutil.rmtree(out, ignore_errors=True)
    finally:
        Path(tmpname).unlink(missing_ok=True)
