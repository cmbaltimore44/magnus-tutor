"""Retrieval over course materials: hybrid keyword (SQLite FTS5) + vector
(sqlite-vec) search, merged with reciprocal rank fusion. Scoped to one course
by default; all courses (including archived ones) on request.

Every passage carries a citation label that matches the physical book
("University Physics Volume 2 §6.3, p. 245") or the notes ("Lecture 4, p. 12").
"""

from __future__ import annotations

import re
import sqlite3

from .db import DB

QUERY_INSTRUCT = "Instruct: Given a student's question about a course, retrieve passages from their notes and textbooks that answer it\nQuery: "


def _load_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


class Retriever:
    def __init__(self, db: DB, models):
        self.db = db
        self.models = models
        with db.lock:
            self.vec = _load_vec(db.conn)
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='porter unicode61')")
        self.dim = int((db.one("SELECT value FROM meta WHERE key = 'embedding_dim'") or {}).get("value") or 0)

    # --- indexing ---------------------------------------------------------------------------

    def _ensure_vec(self, dim: int) -> None:
        if not self.vec:
            return
        if self.dim and self.dim != dim:
            # The embedding model changed size: start the vector index over.
            self.db.execute("DROP TABLE IF EXISTS chunk_vec")
            self.db.execute("UPDATE chunks SET embedding = NULL")
        if self.dim != dim or not self.db.one("SELECT name FROM sqlite_master WHERE name = 'chunk_vec'"):
            self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(chunk_id integer primary key, course text, embedding float[{dim}] distance_metric=cosine)")
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('embedding_dim', ?)", (str(dim),))
            self.dim = dim

    def add_fts(self, chunk_ids: list[int]) -> None:
        if chunk_ids:
            q = ",".join("?" * len(chunk_ids))
            self.db.execute(f"INSERT INTO chunks_fts(rowid, text) SELECT id, text FROM chunks WHERE id IN ({q})", chunk_ids)

    def remove(self, chunk_ids: list[int]) -> None:
        for cid in chunk_ids:
            row = self.db.one("SELECT text FROM chunks WHERE id = ?", (cid,))
            if row:
                self.db.execute("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', ?, ?)", (cid, row["text"]))
            if self.vec and self.dim:
                self.db.execute("DELETE FROM chunk_vec WHERE chunk_id = ?", (cid,))

    def store_embedding(self, chunk_id: int, course: str, blob: bytes) -> None:
        self.db.execute("UPDATE chunks SET embedding = ? WHERE id = ?", (blob, chunk_id))
        if self.vec and self.dim and len(blob) == self.dim * 4:
            self.db.execute("INSERT OR REPLACE INTO chunk_vec(chunk_id, course, embedding) VALUES (?, ?, ?)", (chunk_id, course, blob))

    async def embed_chunks(self, rows: list[dict]) -> None:
        """rows: [{id, course, text, section_path}] → embeddings stored."""
        if not rows:
            return
        texts = [f"{r.get('section_path') or ''}\n{r['text']}".strip()[:6000] for r in rows]
        vecs = await self.models.embed(texts)
        if not vecs:
            return
        self._ensure_vec(len(vecs[0]))
        import numpy as np

        for r, v in zip(rows, vecs):
            blob = np.asarray(v, dtype=np.float32).tobytes()
            self.db.execute("UPDATE chunks SET embedding = ? WHERE id = ?", (blob, r["id"]))
            if self.vec:
                self.db.execute("INSERT OR REPLACE INTO chunk_vec(chunk_id, course, embedding) VALUES (?, ?, ?)", (r["id"], r["course"], blob))

    # --- search -------------------------------------------------------------------------------

    async def search(self, query: str, course: str | None = None, k: int = 5, all_courses: bool = False) -> list[dict]:
        query = query.strip()
        if not query or not self.db.one("SELECT 1 FROM chunks LIMIT 1"):
            return []
        scope = None if all_courses else course
        ranked: dict[int, float] = {}
        for rank, cid in enumerate(self._fts(query, scope, 20)):
            ranked[cid] = ranked.get(cid, 0) + 1 / (60 + rank)
        try:
            vec_ids = await self._vector(query, scope, 20)
        except Exception:
            vec_ids = []
        for rank, cid in enumerate(vec_ids):
            ranked[cid] = ranked.get(cid, 0) + 1.2 / (60 + rank)  # meaning matters a bit more than words
        if not ranked:
            return []
        ids = sorted(ranked, key=ranked.get, reverse=True)
        rows = self._rows(ids[: k * 3])
        out, seen_pages = [], set()
        for cid in ids:
            r = rows.get(cid)
            if not r:
                continue
            key = (r["document_id"], r["page_index"])
            if key in seen_pages:
                continue
            seen_pages.add(key)
            r["score"] = round(ranked[cid], 4)
            out.append(r)
            if len(out) >= k:
                break
        return out

    def _fts(self, query: str, course: str | None, n: int) -> list[int]:
        words = [w for w in re.findall(r"[\w']+", query.lower()) if len(w) > 2][:24]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)
        sql = "SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id = f.rowid WHERE chunks_fts MATCH ?"
        args: list = [match]
        if course:
            sql += " AND c.course = ?"
            args.append(course)
        sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
        args.append(n)
        try:
            return [r["id"] for r in self.db.all(sql, args)]
        except sqlite3.OperationalError:
            return []

    async def _vector(self, query: str, course: str | None, n: int) -> list[int]:
        if not self.dim:
            return []
        import numpy as np

        qv = np.asarray((await self.models.embed([QUERY_INSTRUCT + query]))[0], dtype=np.float32)
        if self.vec:
            sql = "SELECT chunk_id FROM chunk_vec WHERE embedding MATCH ? AND k = ?"
            args: list = [qv.tobytes(), n]
            if course:
                sql += " AND course = ?"
                args.append(course)
            return [r["chunk_id"] for r in self.db.all(sql, args)]
        # Fallback: brute force over stored blobs.
        rows = self.db.all("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL" + (" AND course = ?" if course else ""), [course] if course else [])
        if not rows:
            return []
        mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        sims = mat @ qv / (np.linalg.norm(mat, axis=1) * np.linalg.norm(qv) + 1e-9)
        return [rows[i]["id"] for i in np.argsort(-sims)[:n]]

    def _rows(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        rows = self.db.all(
            f"""SELECT c.id AS chunk_id, c.document_id, c.course, c.section_path, c.page_index, c.page_end, c.printed_page, c.text,
                       d.title, d.kind, d.path FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id IN ({q})""",
            ids,
        )
        out = {}
        for r in rows:
            r["label"] = citation_label(r)
            out[r["chunk_id"]] = r
        return out


def short_title(title: str) -> str:
    t = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", title or "").strip()
    return t[:60] or "Document"


def citation_label(r: dict) -> str:
    page = r.get("printed_page") or (str((r.get("page_index") or 0) + 1))
    title = short_title(r.get("title") or "")
    if r.get("kind") == "textbook":
        m = re.search(r"(?:^|> )(\d+(?:\.\d+)+)\s", (r.get("section_path") or "") + " ")
        sec = f" §{m.group(1)}" if m else ""
        return f"{title}{sec}, p. {page}"
    return f"{title}, p. {page}"
