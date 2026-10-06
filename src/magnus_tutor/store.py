"""Session, message, problem, attempt and upload storage helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from pathlib import Path

from .config import Paths
from .db import DB, loads, now


# --- sessions ------------------------------------------------------------------------------


def create_session(db: DB, course: str | None, mode: str, title: str | None = None, focus: dict | None = None) -> int:
    focus = focus or {}
    return db.insert(
        "sessions", course=course, mode=mode, title=title, started_at=now(), state={},
        focus_label=focus.get("label"), focus_started_at=focus.get("started_at"),
    )


def get_session(db: DB, sid: int) -> dict | None:
    s = db.one("SELECT * FROM sessions WHERE id = ?", (sid,))
    if s:
        s["state"] = loads(s["state"], {})
    return s


def save_state(db: DB, sid: int, state: dict) -> None:
    db.update("sessions", sid, state=state)


def list_sessions(db: DB, course: str | None = None, limit: int = 30) -> list[dict]:
    q = """SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                  (SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'user' ORDER BY id LIMIT 1) AS first_message
           FROM sessions s"""
    args: list = []
    if course:
        q += " WHERE s.course = ?"
        args.append(course)
    q += " ORDER BY COALESCE(s.ended_at, s.started_at) DESC, s.id DESC LIMIT ?"
    args.append(limit)
    rows = db.all(q, args)
    for r in rows:
        r["state"] = loads(r["state"], {})
    return rows


def end_session(db: DB, sid: int, focus_ended_at: float | None = None) -> None:
    db.execute("UPDATE sessions SET ended_at = ?, focus_ended_at = COALESCE(?, focus_ended_at) WHERE id = ?", (now(), focus_ended_at, sid))
    db.execute("UPDATE attempts SET ended_at = ? WHERE session_id = ? AND ended_at IS NULL", (now(), sid))


# --- messages --------------------------------------------------------------------------------


def add_message(db: DB, sid: int, role: str, content: str, images: list[str] | None = None, meta: dict | None = None) -> int:
    return db.insert("messages", session_id=sid, role=role, content=content, images=images or None, meta=meta or {}, created_at=now())


def messages(db: DB, sid: int) -> list[dict]:
    rows = db.all("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (sid,))
    for r in rows:
        r["images"] = loads(r["images"], [])
        r["meta"] = loads(r["meta"], {})
    return rows


# --- problems ------------------------------------------------------------------------------------


def create_problem(db: DB, course: str | None, text: str, source: str = "pasted", kind: str | None = None, status: str = "pending") -> int:
    return db.insert("problems", course=course, text=text, source=source, kind=kind, solver_status=status, created_at=now())


def get_problem(db: DB, pid: int | None) -> dict | None:
    if not pid:
        return None
    pr = db.one("SELECT * FROM problems WHERE id = ?", (pid,))
    if pr:
        pr["reference_solution"] = loads(pr["reference_solution"], None)
        pr["solver_meta"] = loads(pr["solver_meta"], {})
    return pr


def public_problem(pr: dict | None, unlocked: bool) -> dict | None:
    """What the UI may see: the reference only once the full solution is unlocked."""
    if not pr:
        return None
    out = {k: pr[k] for k in ("id", "course", "source", "text", "kind", "confidence", "solver_status", "created_at")}
    out["solver_meta"] = {k: v for k, v in (pr.get("solver_meta") or {}).items() if k in ("elapsed_s", "model", "runs", "agreement", "error", "verification_summary")}
    out["reference_solution"] = pr["reference_solution"] if unlocked else None
    return out


# --- attempts -------------------------------------------------------------------------------------


def start_attempt(db: DB, sid: int, pid: int, course: str | None) -> int:
    return db.insert("attempts", session_id=sid, problem_id=pid, course=course, started_at=now())


def update_attempt(db: DB, sid: int, pid: int, *, hint_level: int, solved: bool, used_full_solution: bool, concept_tags: list[str] | None = None,
                   misconception: str | None = None) -> None:
    row = db.one("SELECT * FROM attempts WHERE session_id = ? AND problem_id = ? ORDER BY id DESC LIMIT 1", (sid, pid))
    if not row:
        return
    vals = {
        "hint_level_reached": max(row["hint_level_reached"], hint_level),
        "solved": int(bool(row["solved"]) or solved),
        "used_full_solution": int(bool(row["used_full_solution"]) or used_full_solution),
        "turns": row["turns"] + 1,
    }
    if concept_tags:
        vals["concept_tags"] = json.dumps(concept_tags)
    if misconception:
        vals["misconception_note"] = misconception
    db.update("attempts", row["id"], **vals)


# --- uploads --------------------------------------------------------------------------------------


def save_upload(p: Paths, data: bytes, filename: str = "image.png") -> str:
    p.uploads.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower() or mimetypes.guess_extension(mimetypes.guess_type(filename)[0] or "") or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".pdf"):
        ext = ".png"
    digest = hashlib.sha256(data).hexdigest()[:24]
    f = p.uploads / f"{digest}{ext}"
    if not f.exists():
        f.write_bytes(data)
    return f.name


def upload_path(p: Paths, upload_id: str) -> Path | None:
    f = (p.uploads / upload_id).resolve()
    return f if f.parent == p.uploads.resolve() and f.exists() else None


def image_b64(p: Paths, upload_id: str) -> str | None:
    f = upload_path(p, upload_id)
    if not f:
        return None
    data = f.read_bytes()
    if f.suffix.lower() in (".heic", ".webp", ".gif"):
        data = _to_png(data) or data
    return base64.b64encode(data).decode()


def _to_png(data: bytes) -> bytes | None:
    try:
        import fitz  # PyMuPDF reads most image formats

        doc = fitz.open(stream=data)
        pix = doc[0].get_pixmap()
        return pix.tobytes("png")
    except Exception:
        return None
