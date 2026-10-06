"""Library: documents, ingestion control, page images with highlights, fixing
transcriptions, exercise lookup, retrieval preview."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..db import loads
from ..ingest import pdf
from ..ingest.worker import IngestService
from .events import bus

router = APIRouter(prefix="/api")


def st(request: Request):
    return request.app.state.tutor


def svc(request: Request) -> IngestService:
    return st(request).extras["ingest"]


def on_startup(st_):
    retriever = st_.extras.get("retriever")
    if retriever is None:
        return
    service = IngestService(st_.p, st_.db, st_.models, lambda: st_.settings, retriever, publish=bus.publish)
    st_.extras["ingest"] = service
    service.start()


def on_shutdown(st_):
    s = st_.extras.get("ingest")
    if s:
        s.stop()


def _doc(d: dict) -> dict:
    d = dict(d)
    d["meta"] = loads(d.get("meta"), {})
    d.pop("toc", None)
    d.pop("page_labels", None)
    d["exists"] = Path(d["path"]).exists()
    return d


@router.get("/library/documents")
async def documents(request: Request, course: str | None = None):
    q = "SELECT * FROM documents" + (" WHERE course = ?" if course else "") + " ORDER BY course, kind, title"
    rows = st(request).db.all(q, [course] if course else [])
    for r in rows:
        r["chunks"] = st(request).db.one("SELECT COUNT(*) AS n FROM chunks WHERE document_id = ?", (r["id"],))["n"]
    return [_doc(r) for r in rows]


@router.get("/library/folders")
async def folders(request: Request):
    home = str(Path.home())
    return [{"course": c, "kind": k, "path": str(f).replace(home, "~", 1), "exists": f.exists()} for c, k, f in svc(request).course_folders()]


@router.post("/library/scan")
async def scan(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    import asyncio

    s = svc(request)
    await asyncio.to_thread(s.remove_missing)
    queued = await asyncio.to_thread(s.scan, body.get("course"))
    s.kick()
    return {"queued": queued}


@router.post("/library/documents/{did}/reingest")
async def reingest(did: int, request: Request):
    svc(request).reingest(did)
    return {"ok": True}


@router.post("/library/documents/{did}/confirm")
async def confirm(did: int, request: Request):
    svc(request).confirm(did)
    return {"ok": True}


@router.delete("/library/documents/{did}")
async def forget(did: int, request: Request):
    """Remove derived data (text, embeddings). The PDF itself is never touched."""
    s = svc(request)
    async with s.lock(did):
        s.delete_document(did)
    return {"ok": True}


@router.post("/library/documents/{did}/repair")
async def repair(did: int, request: Request):
    """Queue vision repair for a page range (shows estimate first in the UI)."""
    body = await request.json()
    pages = range(int(body["from"]), int(body["to"]) + 1)
    n = sum(1 for i in pages if svc(request).request_repair(did, i))
    return {"queued": n}


@router.get("/documents/{did}/pages")
async def pages(did: int, request: Request):
    rows = st(request).db.all("SELECT page_index, printed_page, method, length(text) AS chars FROM pages WHERE document_id = ? ORDER BY page_index", (did,))
    return rows


@router.get("/documents/{did}/pages/{index}/text")
async def page_text(did: int, index: int, request: Request):
    r = st(request).db.one("SELECT * FROM pages WHERE document_id = ? AND page_index = ?", (did, index))
    if not r:
        raise HTTPException(404, "no such page")
    return r


@router.put("/documents/{did}/pages/{index}/text")
async def fix_text(did: int, index: int, request: Request):
    body = await request.json()
    await svc(request).fix_page(did, index, body["text"])
    return {"ok": True}


@router.get("/documents/{did}/file")
async def file(did: int, request: Request):
    d = st(request).db.one("SELECT path FROM documents WHERE id = ?", (did,))
    if not d or not Path(d["path"]).exists():
        raise HTTPException(404, "file not found")
    return FileResponse(d["path"], media_type="application/pdf")


@router.get("/documents/{did}/pages/{index}.png")
async def page_png(did: int, index: int, request: Request, highlight: str | None = None):
    s = st(request)
    d = s.db.one("SELECT path, sha256 FROM documents WHERE id = ?", (did,))
    if not d or not Path(d["path"]).exists():
        raise HTTPException(404, "file not found")
    text = None
    if highlight and highlight.isdigit():
        c = s.db.one("SELECT text FROM chunks WHERE id = ?", (int(highlight),))
        text = c["text"] if c else None
    key = hashlib.sha256(f"{d['sha256']}:{index}:{highlight or ''}".encode()).hexdigest()[:32]
    cache = s.p.cache / "pages"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{key}.png"
    if not f.exists():
        import asyncio

        png = await asyncio.to_thread(pdf.render_png, Path(d["path"]), index, 1.6, text.split("\n\n")[0] if text else None)
        f.write_bytes(png)
        _trim_cache(cache, s.settings["cache"]["max_mb"])
    return Response(f.read_bytes(), media_type="image/png", headers={"Cache-Control": "max-age=86400"})


def _trim_cache(cache: Path, max_mb: int) -> None:
    files = sorted(cache.glob("*.png"), key=lambda x: x.stat().st_atime)
    total = sum(x.stat().st_size for x in files)
    while files and total > max_mb * 2**20:
        old = files.pop(0)
        total -= old.stat().st_size
        old.unlink(missing_ok=True)


@router.post("/library/clear-cache")
async def clear_cache(request: Request):
    import shutil

    c = st(request).p.cache / "pages"
    shutil.rmtree(c, ignore_errors=True)
    return {"ok": True}


@router.post("/library/exercise")
async def exercise(request: Request):
    """POST: looking up an exercise may run a vision repair and write to the database."""
    body = await request.json()
    ex = await svc(request).lookup_exercise(body.get("course"), str(body.get("chapter", "")), str(body.get("number", "")))
    if not ex:
        raise HTTPException(404, "exercise not found")
    return ex


@router.get("/library/search")
async def search(request: Request, q: str, course: str | None = None, all_courses: bool = False, k: int = 6):
    r = st(request).extras.get("retriever")
    return await r.search(q, course=course, k=k, all_courses=all_courses) if r else []
