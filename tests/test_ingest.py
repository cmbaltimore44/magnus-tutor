"""Ingestion and retrieval with a fake model: sections, printed pages, chunks,
exercises, vision caching, confirmation, transcription fixes, course scoping."""

import io

import pymupdf
import pytest

from magnus_tutor import courses as C
from magnus_tutor.config import load_settings
from magnus_tutor.db import get_db
from magnus_tutor.ingest.exercises import parse_lookup
from magnus_tutor.ingest.worker import IngestService
from magnus_tutor.retrieval import Retriever

LOREM = "Gauss's law relates the electric flux through a closed surface to the enclosed charge. " * 6


def make_textbook(path, offset=10):
    doc = pymupdf.open()
    toc = [[1, "Chapter 1 Electric Charges", 1], [2, "1.1 Coulomb's Law", 1], [2, "1.2 Electric Field", 2], [2, "Chapter Review", 3],
           [1, "Chapter 2 Gauss's Law", 4], [2, "2.1 Electric Flux", 4], [2, "2.2 Applying Gauss's Law", 5], [2, "Chapter Review", 6]]
    bodies = [
        "Coulomb's law gives the force between two point charges, inversely proportional to the square of the distance. " * 5,
        "The electric field is the force per unit charge on a small positive test charge. " * 5,
        "Problems\n1. Two charges of 2 uC are 1 m apart. Find the force.\n2. Find the field 3 m from a 5 nC charge.\n",
        "Electric flux measures the field passing through a surface, Phi = E A cos theta. " * 5,
        LOREM,
        "Problems\n1. A sphere of radius 2 m carries 6 nC. Find the field at its surface.\n12. A long wire carries 3 nC/m. Find E at 0.5 m.\n",
    ]
    for i, body in enumerate(bodies):
        page = doc.new_page()
        page.insert_text((72, 60), f"{i + offset}", fontsize=9)
        page.insert_textbox(pymupdf.Rect(72, 90, 540, 760), body, fontsize=11)
    doc.set_toc(toc)
    doc.set_metadata({"title": "Test Physics"})
    doc.save(path)


def make_handwritten(path, pages=2):
    from PIL import Image, ImageDraw

    doc = pymupdf.open()
    for i in range(pages):
        img = Image.new("RGB", (600, 800), "white")
        ImageDraw.Draw(img).text((40, 40), f"{path.stem}: handwritten page {i} about capacitors", fill="black")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        page = doc.new_page()
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)


@pytest.fixture
def svc(p, fake_models):
    mm, fp = fake_models
    fp.reply = lambda messages, **kw: "Lecture notes: the capacitance $C = Q/V$ of the plates. Okafor's trick: double the area, double C."
    db = get_db(p)
    settings = load_settings(p)
    s = IngestService(p, db, mm, lambda: {**settings, "background": {"paused": False, "only_when_plugged_in": False}, "ingest": {"batch_pause_s": 0, "confirm_over_pages": 3}},
                      Retriever(db, mm))
    return s, fp, db


async def test_textbook_sections_pages_chunks_exercises(svc, p):
    s, fp, db = svc
    c = C.create_course("Electricity and Magnetism", short="E&M", kind=["physics"], p=p)
    f = c.folder("textbooks") / "book.pdf"
    make_textbook(f)
    [did] = s.scan(c.slug)
    await s.process(did)
    d = db.one("SELECT * FROM documents WHERE id = ?", (did,))
    assert d["status"] == "done" and d["title"] == "Test Physics" and d["pages"] == 6
    pages = db.all("SELECT page_index, printed_page FROM pages WHERE document_id = ? ORDER BY page_index", (did,))
    assert [x["printed_page"] for x in pages][:3] == ["10", "11", "12"], "printed page numbers from the page headers"
    chunk = db.one("SELECT * FROM chunks WHERE document_id = ? AND section_path LIKE '%2.2%'", (did,))
    assert chunk and "Gauss" in chunk["text"] and chunk["embedding"]
    hits = await s.retriever.search("Gauss's law enclosed charge closed surface", course=c.slug, k=3)
    assert "Test Physics §2.2, p. 14" in [h["label"] for h in hits]
    assert parse_lookup("chapter 2, problem 12") == ("2", "12")
    ex = await s.lookup_exercise(c.slug, "2", "12", read_page=False)
    assert ex and "long wire" in ex["text"] and ex["printed_page"] == "15"


async def test_reingest_reuses_embeddings_and_scopes_courses(svc, p):
    s, fp, db = svc
    a = C.create_course("Electricity and Magnetism", short="E&M", p=p)
    b = C.create_course("Quantum", short="QM", p=p)
    make_textbook(a.folder("textbooks") / "book.pdf")
    [did] = s.scan()
    await s.process(did)
    calls = []
    orig = s.models.ollama.embed

    async def counting(model, texts):
        calls.append(len(texts))
        return await orig(model, texts)

    s.models.ollama.embed = counting
    s.reingest(did)
    await s.process(did)
    assert calls == [], "unchanged chunks keep their embeddings"
    assert await s.retriever.search("Gauss flux", course=b.slug) == []
    assert await s.retriever.search("Gauss flux", all_courses=True)


async def test_handwriting_vision_cached_and_confirmation(svc, p):
    s, fp, db = svc
    c = C.create_course("E&M", p=p)
    make_handwritten(c.folder("notes") / "lecture 3.pdf", pages=2)
    [did] = s.scan()
    await s.process(did)
    vision_calls = [x for x in fp.calls if x["messages"][0].get("images")]
    assert len(vision_calls) == 2
    page = db.one("SELECT * FROM pages WHERE document_id = ? AND page_index = 0", (did,))
    assert page["method"] == "vision" and "capacitance" in page["text"]
    hits = await s.retriever.search("Okafor's trick for capacitance", course=c.slug)
    assert {"lecture 3, p. 1", "lecture 3, p. 2"} >= {h["label"] for h in hits} and hits
    s.reingest(did)
    await s.process(did)
    assert len([x for x in fp.calls if x["messages"][0].get("images")]) == 2, "never re-transcribe unchanged pages"

    make_handwritten(c.folder("notes") / "big notebook.pdf", pages=5)
    [big] = s.scan()
    await s.process(big)
    d = db.one("SELECT * FROM documents WHERE id = ?", (big,))
    assert d["status"] == "needs_confirmation" and '"vision_pages": 5' in d["meta"]
    s.confirm(big)
    await s.process(big)
    assert db.one("SELECT status FROM documents WHERE id = ?", (big,))["status"] == "done"


async def test_fixing_a_transcription_updates_retrieval(svc, p):
    s, fp, db = svc
    c = C.create_course("E&M", p=p)
    make_handwritten(c.folder("notes") / "lecture 4.pdf", pages=1)
    [did] = s.scan()
    await s.process(did)
    await s.fix_page(did, 0, "Corrected: the dielectric constant kappa multiplies the capacitance.")
    assert db.one("SELECT method FROM pages WHERE document_id = ?", (did,))["method"] == "edited"
    hits = await s.retriever.search("dielectric constant kappa", course=c.slug)
    assert hits and "Corrected" in hits[0]["text"]
    s.reingest(did)
    await s.process(did)
    assert "Corrected" in db.one("SELECT text FROM pages WHERE document_id = ?", (did,))["text"], "edits survive re-ingest"


async def test_removing_a_file_forgets_its_derived_data(svc, p):
    s, fp, db = svc
    c = C.create_course("E&M", p=p)
    f = c.folder("textbooks") / "book.pdf"
    make_textbook(f)
    [did] = s.scan()
    await s.process(did)
    f.unlink()
    assert s.remove_missing() == [did]
    assert db.one("SELECT COUNT(*) AS n FROM chunks")["n"] == 0
