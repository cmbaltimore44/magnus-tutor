"""PDFs are parsed in a sandboxed worker process; ingestion caches OCR, drops stale fixes
when a PDF is replaced, and never lets two writers touch one document at once."""

import asyncio
import io

import pymupdf
import pytest

from magnus_tutor import courses as C
from magnus_tutor.ingest import pdf
from test_ingest import make_handwritten, svc  # noqa: F401  (fixture)


def test_worker_reads_pdfs_and_fails_cleanly_on_garbage(tmp_path):
    f = tmp_path / "ok.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Coulomb's law " * 10)
    doc.save(f)
    assert "Coulomb" in pdf.text_of(f)
    assert "Coulomb" in pdf.text_of(f.read_bytes())
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 100)
    with pytest.raises(pdf.PdfWorkerError):
        pdf.text_of(bad)


def test_uploaded_images_convert_in_the_worker():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "GIF")
    png = pdf.image_to_png(buf.getvalue())
    assert png and png.startswith(b"\x89PNG")


async def test_ocr_is_cached_and_replaced_pdfs_drop_stale_fixes(svc, p, monkeypatch):  # noqa: F811
    s, fp, db = svc
    c = C.create_course("E&M", p=p)
    f = c.folder("textbooks") / "scan.pdf"
    make_handwritten(f, pages=2)  # image-only pages: OCR'd as a textbook
    db_kind = "textbook"
    [did] = s.scan()
    db.execute("UPDATE documents SET kind = ? WHERE id = ?", (db_kind, did))
    calls = []
    import magnus_tutor.ingest.worker as W

    real = W.apple_ocr
    monkeypatch.setattr(W, "apple_ocr", lambda png: calls.append(1) or real(png) or "ocr text")
    await s.process(did)
    first = len(calls)
    assert first == 2
    s.reingest(did)
    await s.process(did)
    assert len(calls) == first, "OCR results are cached by page image"

    await s.fix_page(did, 0, "My corrected page about inductors.")
    s.reingest(did)
    await s.process(did)
    assert db.one("SELECT method FROM pages WHERE document_id = ? AND page_index = 0", (did,))["method"] == "edited"
    make_handwritten(f, pages=2, label="a different lecture")  # the file is replaced with new content
    s.register(f, c.slug, "textbook")
    await s.process(did)
    row = db.one("SELECT method, text FROM pages WHERE document_id = ? AND page_index = 0", (did,))
    assert row["method"] != "edited" and "inductors" not in row["text"]


async def test_one_writer_per_document(svc, p):  # noqa: F811
    s, fp, db = svc
    c = C.create_course("E&M", p=p)
    make_handwritten(c.folder("notes") / "lecture.pdf", pages=1)
    [did] = s.scan()
    await s.process(did)
    async with s.lock(did):
        # A tutor turn never waits on a busy document: it gets the current text.
        text = await asyncio.wait_for(s.repair_page(did, 0, foreground=True), 2)
        assert text
        fix = asyncio.create_task(s.fix_page(did, 0, "edited while busy"))
        await asyncio.sleep(0.2)
        assert not fix.done(), "a manual fix waits for the current writer"
    await asyncio.wait_for(fix, 10)
    assert db.one("SELECT method FROM pages WHERE document_id = ?", (did,))["method"] == "edited"
