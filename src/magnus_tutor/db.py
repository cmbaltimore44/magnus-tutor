"""SQLite for generated data (sessions, messages, attempts, documents, chunks,
problems, prompt versions, jobs). Hand-edited things stay in YAML/Markdown.

Embeddings live in a sqlite-vec virtual table keyed by chunk id when the
extension loads; otherwise as float32 blobs searched with NumPy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .config import Paths, paths

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  course TEXT,
  mode TEXT NOT NULL,                 -- office_hours | ask | quiz | code
  title TEXT,
  problem_id INTEGER,
  started_at REAL NOT NULL,
  ended_at REAL,
  focus_label TEXT,                   -- linked Magnus focus round (label + time range)
  focus_started_at REAL,
  focus_ended_at REAL,
  state TEXT NOT NULL DEFAULT '{}'    -- engine state (hint level, gates) as JSON
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                 -- user | assistant | system | tool
  content TEXT NOT NULL,
  images TEXT,                        -- JSON list of upload ids
  meta TEXT NOT NULL DEFAULT '{}',    -- citations, hint level, stats, provider
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS problems (
  id INTEGER PRIMARY KEY,
  course TEXT,
  source TEXT,                        -- "pasted", "Textbook ch. 5 #12", ...
  text TEXT NOT NULL,
  kind TEXT,                          -- math | physics | code | writing | general
  reference_solution TEXT,            -- JSON, never shown unless unlocked
  confidence TEXT,                    -- verified | agreed | uncertain | null (pending)
  solver_status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed | skipped
  solver_meta TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY,
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  problem_id INTEGER REFERENCES problems(id) ON DELETE SET NULL,
  course TEXT,
  concept_tags TEXT NOT NULL DEFAULT '[]',
  hint_level_reached INTEGER NOT NULL DEFAULT 0,
  solved INTEGER NOT NULL DEFAULT 0,
  used_full_solution INTEGER NOT NULL DEFAULT 0,
  misconception_note TEXT,
  turns INTEGER NOT NULL DEFAULT 0,
  started_at REAL NOT NULL,
  ended_at REAL
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  course TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- notes | textbook | other
  path TEXT NOT NULL UNIQUE,
  title TEXT,
  sha256 TEXT,
  pages INTEGER,
  status TEXT NOT NULL DEFAULT 'queued',  -- queued | ingesting | done | failed | paused | needs_confirmation
  progress REAL NOT NULL DEFAULT 0,
  error TEXT,
  toc TEXT,                           -- JSON section tree
  page_labels TEXT,                   -- JSON list: pdf index -> printed page
  meta TEXT NOT NULL DEFAULT '{}',
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_index INTEGER NOT NULL,
  printed_page TEXT,
  sha256 TEXT NOT NULL,               -- hash of the rendered page; transcriptions are cached by it
  text TEXT,
  method TEXT,                        -- embedded | vision | ocr | edited
  image_ref TEXT,
  UNIQUE(document_id, page_index)
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  course TEXT NOT NULL,
  section_path TEXT,
  page_index INTEGER,
  printed_page TEXT,
  page_end INTEGER,
  text TEXT NOT NULL,
  image_ref TEXT,
  embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_course ON chunks(course);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id);

CREATE TABLE IF NOT EXISTS exercises (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  course TEXT NOT NULL,
  chapter TEXT,
  number TEXT NOT NULL,
  page_index INTEGER,
  printed_page TEXT,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS exercises_lookup ON exercises(course, chapter, number);

CREATE TABLE IF NOT EXISTS transcriptions (
  sha256 TEXT PRIMARY KEY,            -- page image hash -> vision transcription (never redone)
  text TEXT NOT NULL,
  model TEXT,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_versions (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,                 -- office_hours, persona, ...
  course TEXT,                        -- null = global
  content TEXT NOT NULL,
  note TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS prompt_versions_name ON prompt_versions(name, course, id);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                 -- ingest | solve | quiz
  status TEXT NOT NULL,               -- queued | running | paused | done | failed | needs_confirmation | cancelled
  payload TEXT NOT NULL DEFAULT '{}',
  progress REAL NOT NULL DEFAULT 0,
  message TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quiz_items (
  id INTEGER PRIMARY KEY,
  session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  course TEXT,
  question TEXT NOT NULL,
  answer TEXT,
  concept TEXT,
  source TEXT,
  student_answer TEXT,
  grade REAL,
  feedback TEXT,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mastery (
  course TEXT NOT NULL,
  concept TEXT NOT NULL,
  score REAL NOT NULL,                -- 0..1
  evidence INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL,
  PRIMARY KEY (course, concept)
);
"""


class DB:
    """Small thread-safe wrapper. One connection, serialized by a lock."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.executemany(sql, rows)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def insert(self, table: str, **values: Any) -> int:
        cols = ", ".join(values)
        qs = ", ".join("?" for _ in values)
        return self.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", [_enc(v) for v in values.values()]).lastrowid

    def update(self, table: str, row_id: int, **values: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in values)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*(_enc(v) for v in values.values()), row_id])

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def _enc(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def loads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (TypeError, json.JSONDecodeError):
        return default


_db: DB | None = None
_db_path: Path | None = None


def get_db(p: Paths | None = None) -> DB:
    global _db, _db_path
    p = p or paths()
    if _db is None or _db_path != p.db:
        _db = DB(p.db)
        _db_path = p.db
    return _db


def now() -> float:
    return time.time()
