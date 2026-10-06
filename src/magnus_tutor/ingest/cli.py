"""`tutor ingest`: ingest now (foreground), or show status."""

from __future__ import annotations

import asyncio
import sys

from .. import runtime
from ..config import load_settings, paths
from ..db import get_db, loads
from ..llm.manager import ModelManager
from ..retrieval import Retriever
from ..setup import bootstrap
from .worker import IngestService


def run(a) -> int:
    p = paths()
    bootstrap(p)
    db = get_db(p)
    if a.status:
        for d in db.all("SELECT * FROM documents ORDER BY course, title"):
            meta = loads(d["meta"], {})
            extra = f" · needs confirmation: {meta.get('vision_pages')} handwritten pages ≈ {meta.get('estimate_s', 0) // 60} min" if d["status"] == "needs_confirmation" else ""
            print(f"{d['course']:<8} {d['kind']:<9} {d['status']:<18} {int(d['progress'] * 100):>3}%  {d['title']}{extra}")
        return 0
    s = load_settings(p)
    with runtime.OllamaFor(s, p) as o:
        if not o["running"]:
            print(f"tutor: {o.get('error', 'Ollama is not running')}", file=sys.stderr)
            return 1
        return _ingest(a, p, db, s)


def _ingest(a, p, db, s) -> int:

    async def go():
        mm = ModelManager(s)
        svc = IngestService(p, db, mm, lambda: s, Retriever(db, mm), publish=lambda k, d: _print(d))
        if a.file:
            from pathlib import Path

            f = Path(a.file).expanduser().resolve()
            if not a.course:
                print("tutor ingest --file needs a course", file=sys.stderr)
                return 1
            kind = "textbook" if "textbook" in str(f.parent).lower() else "notes"
            did = svc.register(f, a.course, kind)
            ids = [did] if did else []
        else:
            ids = svc.scan(a.course)
        for did in ids:
            if a.yes:
                d = db.one("SELECT meta FROM documents WHERE id = ?", (did,))
                meta = loads(d["meta"], {})
                meta["confirmed"] = True
                db.update("documents", did, meta=meta)
            await svc.process(did)
        if not ids:
            print("nothing new to ingest")
        await mm.ollama.unload_all()
        return 0

    return asyncio.run(go())


_last = {}


def _print(d: dict) -> None:
    doc = d.get("document")
    if not doc:
        return
    pct = int(doc["progress"] * 100)
    if _last.get(doc["id"]) == (doc["status"], pct // 10):
        return
    _last[doc["id"]] = (doc["status"], pct // 10)
    print(f"{doc['title'][:50]:<50} {doc['status']:<18} {pct:>3}%", flush=True)
