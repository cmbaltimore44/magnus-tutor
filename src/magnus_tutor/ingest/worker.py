"""Background ingestion: low priority, one document at a time, small batches,
pausable, resumable, and only when plugged in (configurable).

Text comes from three tiers, cheapest first:
  1. the PDF's text layer (pymupdf4llm Markdown)
  2. macOS Vision OCR (Neural Engine, ~0.1 s/page) for scanned pages and for
     pages whose equations dropped out of the text layer
  3. the vision LLM for handwritten notes (asks first for big jobs) and for
     on-demand math repair of pages you actually cite
Transcriptions are cached by page-image hash and never redone.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path

from .. import hardware
from ..config import Paths
from ..courses import list_courses
from ..db import DB, loads, now
from ..llm.manager import ModelManager, Preempted
from . import chunking, exercises, pdf

TRANSCRIBE_NOTES = (
    "Transcribe these handwritten lecture notes exactly as Markdown. Use LaTeX for all math ($...$ inline, $$...$$ display). "
    "Keep headings, lists and the order of the page. If something is illegible, write [illegible]. Output only the transcription."
)
TRANSCRIBE_PAGE = (
    "Transcribe this textbook page faithfully as Markdown. Write every equation and symbol in LaTeX ($...$ inline, $$...$$ display), "
    "keep headings and numbered problems, skip page headers/footers and figures. Output only the transcription."
)


def _background_thread() -> None:
    """Run ingestion threads at macOS background priority (efficiency cores, low I/O priority)."""
    try:
        os.setpriority(os.PRIO_DARWIN_THREAD, 0, os.PRIO_DARWIN_BG)
    except (AttributeError, OSError):
        try:
            os.nice(10)
        except OSError:
            pass


def apple_ocr(png: bytes) -> str | None:
    try:
        from ocrmac import ocrmac
        from PIL import Image
    except ImportError:
        return None
    try:
        res = ocrmac.OCR(Image.open(io.BytesIO(png)), language_preference=["en-US"]).recognize()
    except Exception:
        return None
    # Results are (text, confidence, bbox) with bbox origin at the bottom-left; sort top-to-bottom.
    lines = sorted(res, key=lambda r: (-round(r[2][1] + r[2][3], 2), r[2][0]))
    return "\n".join(r[0] for r in lines).strip()


class IngestService:
    def __init__(self, p: Paths, db: DB, models: ModelManager, settings, retriever, publish=None):
        self.p, self.db, self.models, self.settings = p, db, models, settings
        self.retriever = retriever
        self.publish = publish or (lambda kind, data: None)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, initializer=_background_thread, thread_name_prefix="ingest")
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.watch_task: asyncio.Task | None = None
        self.current: int | None = None

    def cfg(self) -> dict:
        return {"confirm_over_pages": 10, "seconds_per_vision_page": 40, "batch_pause_s": 0.3, "auto_repair": True, **self.settings().get("ingest", {})}

    # --- discovery ------------------------------------------------------------------------------

    def course_folders(self, include_archived: bool = False) -> list[tuple[str, str, Path]]:
        out = []
        for c in list_courses(self.p, include_archived=include_archived):
            for kind in ("notes", "textbooks", "other"):
                f = c.folder(kind)
                if f:
                    out.append((c.slug, "textbook" if kind == "textbooks" else ("notes" if kind == "notes" else "other"), f))
        return out

    def scan(self, course: str | None = None) -> list[int]:
        """Queue new or changed PDFs in the course folders. Files are read in place."""
        queued = []
        for slug, kind, folder in self.course_folders():
            if course and slug != course or not folder.exists():
                continue
            for f in sorted(folder.glob("*.pdf")):
                did = self.register(f, slug, kind)
                if did:
                    queued.append(did)
        return queued  # callers kick the worker (scan may run in a thread)

    def register(self, f: Path, course: str, kind: str) -> int | None:
        try:
            sha = pdf.file_hash(f)
        except OSError:
            return None
        row = self.db.one("SELECT * FROM documents WHERE path = ?", (str(f),))
        if row and row["sha256"] == sha and row["status"] in ("done", "ingesting", "queued", "needs_confirmation", "paused"):
            return None
        if row:
            self.db.update("documents", row["id"], sha256=sha, status="queued", progress=0, error=None, course=course, kind=kind, updated_at=now())
            return row["id"]
        return self.db.insert("documents", course=course, kind=kind, path=str(f), title=f.stem, sha256=sha, status="queued", progress=0, meta={}, updated_at=now())

    def remove_missing(self) -> list[int]:
        gone = []
        for d in self.db.all("SELECT id, path FROM documents"):
            if not Path(d["path"]).exists():
                self.delete_document(d["id"])
                gone.append(d["id"])
        return gone

    def delete_document(self, did: int) -> None:
        ids = [r["id"] for r in self.db.all("SELECT id FROM chunks WHERE document_id = ?", (did,))]
        self.retriever.remove(ids)
        self.db.execute("DELETE FROM documents WHERE id = ?", (did,))

    # --- the worker ------------------------------------------------------------------------------

    def kick(self) -> None:
        self.wake.set()
        if self.task is None or self.task.done():
            self.task = asyncio.get_event_loop().create_task(self._loop())

    def blocked(self) -> str | None:
        bg = self.settings().get("background", {})
        if bg.get("paused"):
            return "paused"
        if bg.get("only_when_plugged_in", True) and hardware.on_battery():
            return "on battery"
        return None

    async def _loop(self) -> None:
        while True:
            reason = self.blocked()
            if reason:
                self._publish_status(f"waiting: {reason}")
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=120)
                except asyncio.TimeoutError:
                    pass
                continue
            doc = self.db.one("SELECT * FROM documents WHERE status = 'queued' ORDER BY id LIMIT 1")
            if doc:
                await self._process_safe(doc["id"])
                continue
            try:
                if await self._resume_embeddings():
                    continue
            except Preempted:
                continue
            except Exception as e:  # embedding model down etc.: back off, never kill the loop
                self._publish_status(f"embeddings paused: {str(e)[:120]}")
                await asyncio.sleep(60)
                continue
            job = self.db.one("SELECT * FROM jobs WHERE kind = 'repair' AND status = 'queued' ORDER BY id LIMIT 1")
            if job and self.cfg()["auto_repair"]:  # _repair_job records its own failures
                await self._repair_job(job)
                await asyncio.sleep(5)  # cool down between vision pages
                continue
            self.wake.clear()
            await self.wake.wait()

    async def _resume_embeddings(self) -> bool:
        """Finish embeddings an interrupted run left behind. Status is kept as it was,
        except a document that was mid-ingest becomes done."""
        row = self.db.one("SELECT c.document_id, d.status FROM chunks c JOIN documents d ON d.id = c.document_id "
                          "WHERE c.embedding IS NULL AND d.status IN ('done', 'ingesting', 'needs_confirmation') LIMIT 1")
        if not row:
            return False
        await self.embed_missing(row["document_id"])
        if row["status"] == "ingesting":
            self.db.update("documents", row["document_id"], status="done", progress=1.0, updated_at=now())
        self._publish_doc(row["document_id"])
        return True

    async def _process_safe(self, did: int) -> None:
        self.current = did
        try:
            await self.process(did)
        except Preempted:
            self.db.update("documents", did, status="queued")
        except Exception as e:
            self.db.update("documents", did, status="failed", error=str(e)[:400], updated_at=now())
            self._publish_doc(did)
        finally:
            self.current = None

    async def _gate(self) -> None:
        """Between batches: yield to tutor turns, honor pause/battery, and breathe."""
        await self.models.wait_idle()
        while self.blocked():
            self._publish_status(f"waiting: {self.blocked()}")
            await asyncio.sleep(10)
        await asyncio.sleep(self.cfg()["batch_pause_s"])

    def _publish_doc(self, did: int) -> None:
        d = self.db.one("SELECT id, course, title, status, progress, error, pages, meta FROM documents WHERE id = ?", (did,))
        if d:
            d["meta"] = loads(d["meta"], {})
            self.publish("job", {"kind": "ingest", "document": d})

    def _publish_status(self, msg: str) -> None:
        self.publish("job", {"kind": "ingest", "status": msg})

    def _progress(self, did: int, value: float, status: str = "ingesting") -> None:
        self.db.update("documents", did, progress=round(value, 3), status=status, updated_at=now())
        self._publish_doc(did)

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    async def process(self, did: int) -> None:
        doc = self.db.one("SELECT * FROM documents WHERE id = ?", (did,))
        path = Path(doc["path"])
        meta = loads(doc["meta"], {})
        self._progress(did, 0.02)
        loop = asyncio.get_running_loop()

        def prog(x):
            loop.call_soon_threadsafe(self._progress, did, 0.02 + 0.28 * x)

        pages, secs, title = await self._run(pdf.extract_pages, path, prog, doc["kind"] == "textbook")
        self.db.update("documents", did, title=title, pages=len(pages), toc=[{"level": s.level, "title": s.title, "start": s.start, "number": s.number} for s in secs],
                       page_labels=[pg.printed for pg in pages])

        # Tier 2: macOS OCR for pages without text, and for textbook pages with dropped equations.
        ocr_targets = [pg for pg in pages if not pg.has_text or (doc["kind"] == "textbook" and pg.gaps)]
        vision_needed = []
        for i, pg in enumerate(ocr_targets):
            sha, png = await self._run(pdf.page_image_hash, path, pg.index)
            pg.extra["sha"] = sha
            cached = self.db.one("SELECT text, model FROM transcriptions WHERE sha256 = ?", (sha,))
            if cached:
                pg.text, pg.extra["method"] = cached["text"], "vision" if cached["model"] != "apple-ocr" else "ocr"
                continue
            if doc["kind"] == "notes" and not pg.has_text:
                vision_needed.append((pg, png))
            text = await self._run(apple_ocr, png)
            if text:
                if pg.has_text:  # textbook gap page: OCR text recovers inline symbols and values
                    pg.extra["text_layer"] = pg.text
                pg.text = text
                pg.extra["method"] = "ocr"
                if not (doc["kind"] == "notes" and not pg.has_text):
                    self.db.execute("INSERT OR REPLACE INTO transcriptions(sha256, text, model, created_at) VALUES (?, ?, 'apple-ocr', ?)", (sha, text, now()))
            if i % 20 == 0:
                self._progress(did, 0.3 + 0.1 * (i + 1) / max(1, len(ocr_targets)))
                await asyncio.sleep(0)

        # Tier 3: the vision LLM for handwriting (big jobs need confirmation).
        if vision_needed:
            c = self.cfg()
            if len(vision_needed) > c["confirm_over_pages"] and not meta.get("confirmed"):
                est = int(len(vision_needed) * c["seconds_per_vision_page"])
                meta.update({"vision_pages": len(vision_needed), "estimate_s": est})
                self._save_pages(did, pages)  # OCR text is searchable meanwhile
                await self._chunk_and_embed(did, doc, pages)
                self.db.update("documents", did, status="needs_confirmation", meta=meta, progress=1.0, updated_at=now())
                self._publish_doc(did)
                return
            for j, (pg, png) in enumerate(vision_needed):
                await self._gate()
                text = await self._vision(png, TRANSCRIBE_NOTES)
                if text:
                    pg.text = text
                    pg.extra["method"] = "vision"
                    self.db.execute("INSERT OR REPLACE INTO transcriptions(sha256, text, model, created_at) VALUES (?, ?, ?, ?)",
                                    (pg.extra["sha"], text, self.models.model_for("vision"), now()))
                self._progress(did, 0.4 + 0.3 * (j + 1) / len(vision_needed))

        self._save_pages(did, pages)
        await self._chunk_and_embed(did, doc, pages)
        if doc["kind"] == "textbook":
            self._save_exercises(did, doc["course"], pages)
        meta.pop("vision_pages", None)
        meta.pop("estimate_s", None)
        meta["gap_pages"] = sum(1 for pg in pages if pg.gaps)
        self.db.update("documents", did, status="done", progress=1.0, error=None, meta=meta, updated_at=now())
        self._publish_doc(did)

    async def _vision(self, png: bytes, instruction: str, foreground: bool = False) -> str:
        import base64

        msgs = [{"role": "user", "content": instruction, "images": [base64.b64encode(png).decode()]}]
        out = []
        async for c in self.models.chat("vision", msgs, background=not foreground, options={"temperature": 0, "num_predict": 3000}):
            out.append(c.text)
        return "".join(out).strip()

    def _save_pages(self, did: int, pages: list[pdf.PageInfo]) -> None:
        rows = []
        for pg in pages:
            method = pg.extra.get("method") or ("embedded" if pg.has_text else "none")
            if method == "embedded" and pg.gaps:
                method = "embedded-gaps"
            sha = pg.extra.get("sha") or hashlib.sha256(pg.text.encode()).hexdigest()
            rows.append((did, pg.index, pg.printed, sha, pg.text, method, f"{did}:{pg.index}", ))
        existing = {r["page_index"]: r for r in self.db.all("SELECT page_index, method, text FROM pages WHERE document_id = ?", (did,))}
        for r in rows:
            prev = existing.get(r[1])
            if prev and (prev["method"] == "edited" or (prev["method"] == "vision" and r[5] != "vision")):
                continue  # the student's fixes always win; vision repairs beat cheaper text
            self.db.execute(
                "INSERT INTO pages(document_id, page_index, printed_page, sha256, text, method, image_ref) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(document_id, page_index) DO UPDATE SET printed_page=excluded.printed_page, sha256=excluded.sha256, text=excluded.text, method=excluded.method",
                r,
            )
        # Use stored fixes for chunking.
        fixed = {r["page_index"]: r for r in self.db.all("SELECT page_index, text, method FROM pages WHERE document_id = ? AND method IN ('edited', 'vision')", (did,))}
        for pg in pages:
            if pg.index in fixed:
                pg.extra["method"] = fixed[pg.index]["method"]
        for pg in pages:
            if pg.index in fixed:
                pg.text = fixed[pg.index]["text"]

    async def _chunk_and_embed(self, did: int, doc: dict, pages: list[pdf.PageInfo]) -> None:
        olds = self.db.all("SELECT id, page_index, text, embedding FROM chunks WHERE document_id = ?", (did,))
        # Unchanged chunks keep their embeddings, so an interrupted or repeated ingest is cheap.
        reuse = {(r["page_index"], hashlib.sha1(r["text"].encode()).hexdigest()): r["embedding"] for r in olds if r["embedding"]}
        self.retriever.remove([r["id"] for r in olds])
        self.db.execute("DELETE FROM chunks WHERE document_id = ?", (did,))
        chunks = chunking.chunk_textbook(pages) if doc["kind"] == "textbook" else chunking.chunk_notes(pages)
        rows = [(did, doc["course"], c.section_path, c.page_index, c.printed_page, c.page_end, c.text, f"{did}:{c.page_index}") for c in chunks]
        self.db.executemany("INSERT INTO chunks(document_id, course, section_path, page_index, printed_page, page_end, text, image_ref) VALUES (?,?,?,?,?,?,?,?)", rows)
        new = self.db.all("SELECT id, course, page_index, text FROM chunks WHERE document_id = ? ORDER BY id", (did,))
        self.retriever.add_fts([r["id"] for r in new])
        for r in new:
            blob = reuse.get((r["page_index"], hashlib.sha1(r["text"].encode()).hexdigest()))
            if blob:
                self.retriever.store_embedding(r["id"], r["course"], blob)
        await self.embed_missing(did)

    async def embed_missing(self, did: int, batch: int = 16) -> None:
        todo = self.db.all("SELECT id, course, text, section_path FROM chunks WHERE document_id = ? AND embedding IS NULL ORDER BY id", (did,))
        total = len(todo)
        for i in range(0, total, batch):
            await self._gate()
            await self.retriever.embed_chunks(todo[i : i + batch])
            self._progress(did, 0.7 + 0.3 * min(1.0, (i + batch) / max(1, total)))

    def _save_exercises(self, did: int, course: str, pages: list[pdf.PageInfo]) -> None:
        self.db.execute("DELETE FROM exercises WHERE document_id = ?", (did,))
        exs = exercises.find_exercises(pages)
        self.db.executemany(
            "INSERT INTO exercises(document_id, course, chapter, number, page_index, printed_page, text) VALUES (?,?,?,?,?,?,?)",
            [(did, course, e.chapter, e.number if e.kind != "conceptual questions" else f"q{e.number}", e.page_index, e.printed_page, e.text) for e in exs],
        )

    # --- confirmations, fixes, repair --------------------------------------------------------------

    def confirm(self, did: int) -> None:
        d = self.db.one("SELECT meta FROM documents WHERE id = ?", (did,))
        meta = loads(d["meta"], {}) if d else {}
        meta["confirmed"] = True
        self.db.update("documents", did, status="queued", meta=meta, updated_at=now())
        self.kick()

    def reingest(self, did: int) -> None:
        self.db.update("documents", did, status="queued", progress=0, error=None, updated_at=now())
        self.kick()

    async def fix_page(self, did: int, page_index: int, text: str) -> None:
        """The student corrected a transcription: keep it, re-chunk that page's section."""
        self.db.execute("UPDATE pages SET text = ?, method = 'edited' WHERE document_id = ? AND page_index = ?", (text, did, page_index))
        await self.rechunk_section(did, page_index)

    async def rechunk_section(self, did: int, page_index: int, foreground: bool = False) -> None:
        """Re-chunk and re-embed only the section containing `page_index` (cheap)."""
        doc = self.db.one("SELECT * FROM documents WHERE id = ?", (did,))
        secs = await self._run(pdf.sections_of, Path(doc["path"])) if Path(doc["path"]).exists() else []
        sec = pdf.deepest_section(secs, page_index)
        lo, hi = (sec.start, sec.end - 1) if sec and doc["kind"] == "textbook" else (page_index, page_index)
        old = [r["id"] for r in self.db.all("SELECT id FROM chunks WHERE document_id = ? AND page_index <= ? AND page_end >= ?", (did, hi, lo))]
        if old:
            lo = min(lo, self.db.one(f"SELECT MIN(page_index) AS m FROM chunks WHERE id IN ({','.join('?' * len(old))})", old)["m"])
            hi = max(hi, self.db.one(f"SELECT MAX(page_end) AS m FROM chunks WHERE id IN ({','.join('?' * len(old))})", old)["m"])
            old = [r["id"] for r in self.db.all("SELECT id FROM chunks WHERE document_id = ? AND page_index >= ? AND page_end <= ?", (did, lo, hi))]
        self.retriever.remove(old)
        if old:
            self.db.execute(f"DELETE FROM chunks WHERE id IN ({','.join('?' * len(old))})", old)
        rows = self.db.all("SELECT * FROM pages WHERE document_id = ? AND page_index BETWEEN ? AND ? ORDER BY page_index", (did, lo, hi))
        pages = [pdf.PageInfo(index=r["page_index"], printed=r["printed_page"] or "", text=r["text"] or "", has_text=bool(r["text"]), gaps=False,
                              section=pdf.deepest_section(secs, r["page_index"])) for r in rows]
        chunks = chunking.chunk_textbook(pages) if doc["kind"] == "textbook" else chunking.chunk_notes(pages)
        max_before = (self.db.one("SELECT MAX(id) AS m FROM chunks") or {}).get("m") or 0
        self.db.executemany(
            "INSERT INTO chunks(document_id, course, section_path, page_index, printed_page, page_end, text, image_ref) VALUES (?,?,?,?,?,?,?,?)",
            [(did, doc["course"], c.section_path, c.page_index, c.printed_page, c.page_end, c.text, f"{did}:{c.page_index}") for c in chunks],
        )
        new = [r["id"] for r in self.db.all("SELECT id FROM chunks WHERE document_id = ? AND id > ? ORDER BY id", (did, max_before))]
        self.retriever.add_fts(new)  # only the rows inserted just now
        todo = self.db.all(f"SELECT id, course, text, section_path FROM chunks WHERE id IN ({','.join('?' * len(new))})", new) if new else []
        for i in range(0, len(todo), 16):
            if not foreground:
                await self._gate()
            await self.retriever.embed_chunks(todo[i : i + 16])

    async def rechunk_document(self, did: int) -> None:
        doc = self.db.one("SELECT * FROM documents WHERE id = ?", (did,))
        before = doc["status"]
        secs = await self._run(pdf.sections_of, Path(doc["path"])) if Path(doc["path"]).exists() else []
        rows = self.db.all("SELECT * FROM pages WHERE document_id = ? ORDER BY page_index", (did,))
        pages = [pdf.PageInfo(index=r["page_index"], printed=r["printed_page"] or "", text=r["text"] or "", has_text=bool(r["text"]), gaps=r["method"] == "embedded-gaps",
                              section=pdf.deepest_section(secs, r["page_index"])) for r in rows]
        await self._chunk_and_embed(did, doc, pages)
        self.db.update("documents", did, status=before if before != "ingesting" else "done", progress=1.0, updated_at=now())
        self._publish_doc(did)

    def request_repair(self, did: int, page_index: int) -> bool:
        """Queue a vision repair of a cited page whose equations dropped out (idle + plugged in only)."""
        page = self.db.one("SELECT method FROM pages WHERE document_id = ? AND page_index = ?", (did, page_index))
        if not page or page["method"] not in ("embedded-gaps", "ocr"):
            return False
        payload = json.dumps({"document_id": did, "page_index": page_index})
        if self.db.one("SELECT 1 FROM jobs WHERE kind = 'repair' AND payload = ? AND status IN ('queued', 'running', 'done')", (payload,)):
            return False
        self.db.insert("jobs", kind="repair", status="queued", payload=payload, created_at=now(), updated_at=now())
        self.kick()
        return True

    async def repair_page(self, did: int, page_index: int, foreground: bool = False) -> str:
        """Vision-transcribe one page. foreground=True when a student is waiting on it (a
        textbook lookup inside a tutor turn): no background gates, no waiting for idle."""
        doc = self.db.one("SELECT * FROM documents WHERE id = ?", (did,))
        sha, png = await self._run(pdf.page_image_hash, Path(doc["path"]), page_index)
        cached = self.db.one("SELECT text FROM transcriptions WHERE sha256 = ? AND model != 'apple-ocr'", (sha,))
        text = cached["text"] if cached else await self._vision(png, TRANSCRIBE_PAGE, foreground=foreground)
        if text:
            if not cached:
                self.db.execute("INSERT OR REPLACE INTO transcriptions(sha256, text, model, created_at) VALUES (?, ?, ?, ?)", (sha, text, self.models.model_for("vision"), now()))
            self.db.execute("UPDATE pages SET text = ?, method = 'vision' WHERE document_id = ? AND page_index = ?", (text, did, page_index))
            await self.rechunk_section(did, page_index, foreground=foreground)
        return text

    async def _repair_job(self, job: dict) -> None:
        payload = json.loads(job["payload"])
        self.db.update("jobs", job["id"], status="running", updated_at=now())
        try:
            await self._gate()
            await self.repair_page(payload["document_id"], payload["page_index"])
            self.db.update("jobs", job["id"], status="done", progress=1.0, updated_at=now())
        except Preempted:
            self.db.update("jobs", job["id"], status="queued", updated_at=now())
        except Exception as e:
            self.db.update("jobs", job["id"], status="failed", message=str(e)[:300], updated_at=now())
        self.publish("job", {"kind": "repair", "job": job["id"]})

    # --- exercise lookup ------------------------------------------------------------------------------

    async def lookup_exercise(self, course: str | None, chapter: str, number: str, read_page: bool = True) -> dict | None:
        q = "SELECT e.*, d.title, d.path, d.kind FROM exercises e JOIN documents d ON d.id = e.document_id WHERE e.chapter = ? AND e.number = ?"
        args = [chapter, number]
        if course:
            q += " AND e.course = ?"
            args.append(course)
        ex = self.db.one(q + " ORDER BY e.id LIMIT 1", args)
        if not ex:
            return None
        page = self.db.one("SELECT method, text FROM pages WHERE document_id = ? AND page_index = ?", (ex["document_id"], ex["page_index"]))
        text = ex["text"]
        if read_page and page and page["method"] in ("embedded-gaps", "ocr"):
            # Values and symbols may be missing from the text layer: read this one page properly.
            full = await self.repair_page(ex["document_id"], ex["page_index"], foreground=True)
            found = exercises.find_exercises([pdf.PageInfo(index=ex["page_index"], printed=ex["printed_page"], text=full, has_text=True, gaps=False,
                                                           section=pdf.Section(1, "", 0, 0, chapter=chapter), extra={"plain": "Problems\n" + full})])
            hit = next((e for e in found if e.number == number.lstrip("q")), None)
            if hit:
                text = hit.text
                self.db.execute("UPDATE exercises SET text = ? WHERE id = ?", (text, ex["id"]))
        from ..retrieval import short_title

        return {"text": text, "label": f"{short_title(ex['title'])} ch. {chapter} #{number.lstrip('q')}, p. {ex['printed_page']}", "document_id": ex["document_id"],
                "page_index": ex["page_index"], "printed_page": ex["printed_page"]}

    # --- watching folders -------------------------------------------------------------------------------

    async def watch(self) -> None:
        """OS file events on every course folder; new or changed PDFs are queued."""
        from watchfiles import Change, awatch

        folders = [str(f) for _, _, f in self.course_folders() if f.exists()]
        if not folders:
            return
        async for changes in awatch(*folders, recursive=False, debounce=1500, step=200):
            touched = {Path(p) for ch, p in changes if p.lower().endswith(".pdf") and ch != Change.deleted}
            for f in touched:
                for slug, kind, folder in self.course_folders():
                    if f.parent == folder:
                        await asyncio.to_thread(self.register, f, slug, kind)  # hashing a big PDF takes a while
            if any(ch == Change.deleted for ch, _ in changes):
                await asyncio.to_thread(self.remove_missing)
            self.kick()

    def start(self) -> None:
        loop = asyncio.get_event_loop()
        # Work interrupted by a shutdown resumes (chunks already embedded are kept).
        self.db.execute("UPDATE documents SET status = 'queued' WHERE status = 'ingesting'")
        self.db.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running' AND kind = 'repair'")
        self.watch_task = loop.create_task(self._startup())

    async def _startup(self) -> None:
        await asyncio.to_thread(self.remove_missing)
        await asyncio.to_thread(self.scan)  # hashes PDFs: keep it off the event loop
        self.kick()
        await self.watch()

    def refresh_watch(self) -> None:
        """A course was added or changed: watch its folders too and pick up what's there."""
        if self.watch_task:
            self.watch_task.cancel()
        self.watch_task = asyncio.get_event_loop().create_task(self._startup())

    def stop(self) -> None:
        for t in (self.task, self.watch_task):
            if t:
                t.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)

