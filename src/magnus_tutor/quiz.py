"""Quiz mode and mastery.

Questions are written only from the student's materials (retrieved passages),
graded against those sources (plus SymPy/numeric checks for math answers), and
every graded answer or office-hours attempt nudges a per-concept mastery score.
"""

from __future__ import annotations

import json
import random

from . import prompts
from .config import Paths
from .context import base_variables, format_sources
from .courses import Course
from .db import DB, now
from .solver import parse_json
from .tools import mathcheck as M

QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "answer": {"type": "string"},
        "concept": {"type": "string"},
        "source": {"type": "string"},
        "kind": {"type": "string"},
    },
    "required": ["question", "answer", "concept"],
}
GRADE_SCHEMA = {
    "type": "object",
    "properties": {"grade": {"type": "number"}, "feedback": {"type": "string"}, "missing": {"type": "string"}},
    "required": ["grade", "feedback"],
}

ALPHA = 0.35  # how fast mastery moves toward new evidence


def observe(db: DB, course: str | None, concepts: list[str], score: float) -> None:
    """Move each concept's mastery toward `score` (0..1)."""
    if not course:
        return
    score = max(0.0, min(1.0, score))
    for c in {x.strip() for x in concepts if x and x.strip()}:
        row = db.one("SELECT score, evidence FROM mastery WHERE course = ? AND concept = ?", (course, c))
        if row:
            db.execute("UPDATE mastery SET score = ?, evidence = evidence + 1, updated_at = ? WHERE course = ? AND concept = ?",
                       (round(row["score"] * (1 - ALPHA) + score * ALPHA, 4), now(), course, c))
        else:
            db.execute("INSERT INTO mastery(course, concept, score, evidence, updated_at) VALUES (?, ?, ?, 1, ?)", (course, c, round(0.5 * (1 - ALPHA) + score * ALPHA, 4), now()))


def attempt_score(hint_level: int, solved: bool, used_solution: bool) -> float | None:
    if used_solution:
        return 0.2
    if solved:
        return max(0.3, 1.0 - 0.15 * hint_level)
    return None


def pick_topic(db: DB, course: Course | None, requested: str | None) -> str:
    if requested and requested.strip():
        return requested.strip()
    if course:
        weak = db.all("SELECT concept FROM mastery WHERE course = ? AND score < 0.6 ORDER BY score ASC LIMIT 5", (course.slug,))
        pool = [w["concept"] for w in weak] + list(course.topics)
        if pool:
            return random.choice(pool[:8])
    return "the most important idea in the student's recent material"


class Quizzer:
    def __init__(self, p: Paths, db: DB, models, retriever=None):
        self.p, self.db, self.models, self.retriever = p, db, models, retriever

    async def next_question(self, sid: int, course: Course | None, topic: str | None = None) -> dict:
        topic = pick_topic(self.db, course, topic)
        passages = []
        if self.retriever:
            try:
                passages = await self.retriever.search(topic, course=course.slug if course else None, k=4)
            except Exception:
                passages = []
        asked = [r["question"] for r in self.db.all("SELECT question FROM quiz_items WHERE session_id = ? ORDER BY id DESC LIMIT 6", (sid,))]
        v = base_variables(course, self.p)
        v["quiz_topic"] = f": {topic}"
        v["retrieved_context"] = format_sources(passages)
        sysmsg = prompts.get("persona", v, course, self.p) + "\n\n" + prompts.get("quiz", v, course, self.p)
        user = (
            f"Sources:\n{v['retrieved_context']}\n\nWrite ONE new quiz question about {topic}"
            + (" answerable from the sources above" if passages else " (no sources matched; use standard course knowledge and set source to \"general knowledge\")")
            + ". Mix conceptual and computational questions across the quiz. Don't repeat these: " + json.dumps(asked)
            + '\nReturn JSON: {"question": "...", "answer": "the expected answer, concise, with units", "concept": "the concept tested (short)", '
              '"source": "the citation label of the passage used, exactly as given", "kind": "conceptual|computational"}'
        )
        out = []
        async for c in self.models.chat("tutor", [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}], fmt=QUESTION_SCHEMA,
                                        options={"temperature": 0.7, "num_predict": 900}):
            out.append(c.text)
        q = _loads(out)
        if not q or not q.get("question"):
            raise RuntimeError("couldn't write a question; try another topic")
        src = q.get("source") or ""
        match = next((ps for ps in passages if ps["label"] == src), passages[0] if passages and src != "general knowledge" else None)
        qid = self.db.insert("quiz_items", session_id=sid, course=course.slug if course else None, question=q["question"], answer=q.get("answer", ""),
                             concept=q.get("concept") or topic, source=json.dumps(_src(match)) if match else None, created_at=now())
        return {"id": qid, "question": q["question"], "concept": q.get("concept") or topic, "kind": q.get("kind"), "source": _src(match) if match else None}

    async def grade(self, item_id: int, student_answer: str, course: Course | None) -> dict:
        item = self.db.one("SELECT * FROM quiz_items WHERE id = ?", (item_id,))
        if not item:
            raise KeyError(item_id)
        src = json.loads(item["source"]) if item["source"] else None
        # Real math first: a matching value or expression is correct whatever the wording.
        math_ok = None
        if item["answer"]:
            import asyncio

            try:  # in a thread with a time limit, so no answer can stall the server
                math_ok = await asyncio.wait_for(asyncio.to_thread(M.matches_reference, student_answer, item["answer"]), 5)
            except Exception:
                math_ok = None
        user = (
            f"Question: {item['question']}\nExpected answer: {item['answer']}\n"
            + (f"Source passage ({src['label']}):\n{src.get('text', '')[:1500]}\n" if src else "")
            + f"Student's answer: {student_answer}\n"
            + (f"Automatic math check: the student's value/expression {'MATCHES' if math_ok else 'does NOT match'} the expected answer.\n" if math_ok is not None else "")
            + 'Grade it. Return JSON: {"grade": 0 to 1 (partial credit allowed), "feedback": "2-4 sentences: what was right, what was missing or wrong, '
              'citing the source label if there is one", "missing": "the key idea they missed, or empty"}'
        )
        out = []
        async for c in self.models.chat("tutor", [{"role": "user", "content": user}], fmt=GRADE_SCHEMA, options={"temperature": 0.1, "num_predict": 600}):
            out.append(c.text)
        g = _loads(out) or {"grade": 0.0, "feedback": "I couldn't grade that automatically."}
        grade = float(g.get("grade") or 0)
        if math_ok is True:
            grade = max(grade, 0.9)
        elif math_ok is False:
            grade = min(grade, 0.5)
        grade = round(max(0.0, min(1.0, grade)), 2)
        self.db.update("quiz_items", item_id, student_answer=student_answer, grade=grade, feedback=g.get("feedback", ""))
        observe(self.db, item["course"], [item["concept"]], grade)
        return {"id": item_id, "grade": grade, "feedback": g.get("feedback", ""), "missing": g.get("missing", ""), "answer": item["answer"], "source": src}


def _src(ps: dict | None) -> dict | None:
    if not ps:
        return None
    return {k: ps.get(k) for k in ("label", "document_id", "page_index", "printed_page", "chunk_id", "section_path", "text")}


def _loads(chunks: list[str]) -> dict | None:
    raw = "".join(chunks)
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return parse_json(raw.replace("{", '{"final_answer": "",', 1)) if "{" in raw else None
