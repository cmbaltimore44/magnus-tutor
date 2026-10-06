"""The tutor engine: one turn = classify → gates → context → stream (leak-checked) → log.

Modes:
  office_hours  Socratic problem sets (writing courses get the writing coach)
  code          office hours for code, with real run results as ground truth
  ask           questions grounded in notes and textbooks, with citations
  quiz          see quiz.py

Events yielded to the caller (SSE in the web app, plain text in Magnus):
  {"type": "meta", ...}        intent, hint level, solver status, sources
  {"type": "token", "text"}    streamed reply text (already leak-checked)
  {"type": "reset", "reason"}  the draft was withdrawn; a stricter reply follows
  {"type": "done", ...}        message id, stats, final state
  {"type": "error", "message"}
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator, Callable

from .. import store
from ..config import Paths
from ..context import system_prompt
from ..courses import Course, load_course
from ..db import DB
from ..llm.base import ProviderError
from ..llm.manager import ModelManager
from ..tools import mathcheck as M
from . import intent as I
from .ladder import LEVEL_INSTRUCTIONS, LEVEL_NAMES, GateState, decide, new_problem_state
from .leak import SAFE_FALLBACK, LeakChecker, stricter_instruction

HISTORY_MESSAGES = 14
MAX_MESSAGE_CHARS = 4000
_BOUNDARY = re.compile(r"(?<=[.!?:])\s+|\n")

TRANSCRIBE_PROBLEM = (
    "Transcribe the problem in this image exactly, as Markdown with LaTeX for math ($...$ inline, $$...$$ display). "
    "Include all given values, units, and sub-parts. Output only the transcription."
)
TRANSCRIBE_WORK = (
    "Transcribe the student's handwritten work in this image as Markdown with LaTeX for math. "
    "Keep their steps in order, including mistakes. Output only the transcription."
)


class Tutor:
    def __init__(
        self,
        p: Paths,
        db: DB,
        models: ModelManager,
        settings: Callable[[], dict],
        *,
        retriever=None,
        solver=None,
        ingest=None,
        overrides: dict | None = None,
    ):
        self.p, self.db, self.models, self.settings = p, db, models, settings
        self.retriever = retriever
        self.solver = solver
        self.ingest = ingest
        self.overrides = overrides  # unsaved prompt drafts (prompt tester)

    # --- public API -------------------------------------------------------------------------

    def create_session(self, course: str | None, mode: str = "office_hours", title: str | None = None, focus: dict | None = None) -> int:
        return store.create_session(self.db, course, mode, title, focus)

    async def turn(
        self,
        sid: int,
        text: str,
        images: list[str] | None = None,
        *,
        action: str | None = None,
        code_context: str | None = None,
        provider: str = "ollama",
    ) -> AsyncIterator[dict]:
        images = images or []
        session = store.get_session(self.db, sid)
        if not session:
            yield {"type": "error", "message": "no such session"}
            return
        course = load_course(session["course"], self.p) if session["course"] else None
        try:
            # The whole turn is one foreground block, so a background solver can't slip into
            # the gaps between its steps (retrieval, transcription, the reply) and swap models.
            async with self.models.foreground():
                if session["mode"] in ("office_hours", "code"):
                    async for ev in self._office_hours(session, course, text, images, action, code_context, provider):
                        yield ev
                else:
                    async for ev in self._ask(session, course, text, images, provider):
                        yield ev
        except ProviderError as e:
            yield {"type": "error", "message": str(e)}

    async def escalate(self, sid: int, message_id: int) -> AsyncIterator[dict]:
        """Redo one assistant reply with the cloud model: same history, same hint level,
        same leak check. The new reply is stored after it and marked as cloud."""
        msgs_all = store.messages(self.db, sid)
        idx = next((i for i, m in enumerate(msgs_all) if m["id"] == message_id and m["role"] == "assistant"), None)
        uidx = next((j for j in range(idx - 1, -1, -1) if msgs_all[j]["role"] == "user"), None) if idx else None
        if idx is None or uidx is None:
            yield {"type": "error", "message": "no such reply"}
            return
        user = msgs_all[uidx]
        session = store.get_session(self.db, sid)
        course = load_course(session["course"], self.p) if session["course"] else None
        meta = msgs_all[idx]["meta"]
        level = int(meta.get("hint_level", 4 if session["mode"] == "ask" else 0))
        state = GateState.from_dict(session["state"])
        problem = store.get_problem(self.db, state.problem_id)
        reference = problem.get("reference_solution") if problem else None
        passages = meta.get("sources") or []
        if session["mode"] in ("office_hours", "code"):
            state_vars = {
                "problem": problem["text"] if problem else "", "attempt_status": "", "hint_level": str(level), "hint_level_name": LEVEL_NAMES[level],
                "hint_instruction": LEVEL_INSTRUCTIONS[level],
                "reference_solution": _reference_text(reference, level), "solution_confidence": (problem or {}).get("confidence") or "none", "verification_note": "",
            }
            sysmsg = system_prompt(session["mode"], course, passages=passages, state_vars=state_vars, p=self.p, overrides=self.overrides)
        else:
            sysmsg = system_prompt("ask", course, passages=passages, p=self.p, overrides=self.overrides)
        history = [{"role": m["role"], "content": m["content"]} for m in msgs_all[:uidx] if m["role"] in ("user", "assistant")][-HISTORY_MESSAGES:]
        msgs = [{"role": "system", "content": sysmsg}, *history, _user_msg(user["content"], user.get("images") or [], self.p)]
        checker = LeakChecker(reference, problem["text"] if problem else "") if self.settings()["gates"].get("output_check", True) and level < 4 and session["mode"] != "ask" else None
        yield {"type": "meta", "hint_level": level, "provider": "anthropic", "sources": passages}
        reply, stats = "", {}
        try:
            async for ev in self._gated_stream("tutor", msgs, checker, level, "anthropic"):
                if ev["type"] == "final":
                    reply, stats = ev["text"], ev["stats"]
                else:
                    yield ev
        except ProviderError as e:
            yield {"type": "error", "message": str(e)}
            return
        mid = store.add_message(self.db, sid, "assistant", reply, None, {"hint_level": level, "stats": stats, "sources": passages, "provider": "anthropic",
                                                                         "model": stats.get("model"), "escalated_from": message_id})
        yield {"type": "done", "message_id": mid, "stats": stats}

    # --- office hours ------------------------------------------------------------------------

    async def _office_hours(self, session, course: Course | None, text, images, action, code_context, provider) -> AsyncIterator[dict]:
        sid = session["id"]
        gates = self.settings()["gates"]
        state = GateState.from_dict(session["state"])
        problem = store.get_problem(self.db, state.problem_id)
        first_turn = False
        looked_up = None

        user_text = text
        if action == "unlock_now":
            user_text = text or "Show me the full solution now."
        elif action == "unlock_after":
            user_text = text or "I'll try once more first."

        intent = I.classify(user_text, has_images=bool(images), solution_offer_pending=state.solution_offer_pending)
        if action == "unlock_now":
            intent = I.Intent("solution_choice", solution_when="now")
            state.solution_offer_pending = True
        elif action == "unlock_after":
            intent = I.Intent("solution_choice", solution_when="after")
        elif action == "ask_solution":
            intent = I.Intent("ask_solution")

        # A new problem: the first message of a session, an explicit "new problem", or the button.
        if problem is None or action == "new_problem" or intent.kind == "new_problem":
            problem_text = I._NEW_PROBLEM.sub("", user_text).strip(" :.-") if intent.kind == "new_problem" else user_text
            source = "pasted"
            looked_up = await self._lookup_exercise(course, user_text)
            if looked_up:
                yield {"type": "status", "message": f"Found {looked_up['label']}"}
                problem_text = looked_up["text"]
                source = looked_up["label"]
            if images and len(problem_text) < 40:
                yield {"type": "status", "message": "Reading the problem from your image…"}
                transcribed = await self._transcribe(images[0], TRANSCRIBE_PROBLEM)
                problem_text = (problem_text + "\n\n" + transcribed).strip() if transcribed else problem_text
            kind = _problem_kind(course, session["mode"], problem_text)
            run_solver = self.solver is not None and kind != "writing" and self.settings()["solver"].get("enabled", True)
            pid = store.create_problem(self.db, session["course"], problem_text, source=source, kind=kind, status="pending" if run_solver else "skipped")
            store.start_attempt(self.db, sid, pid, session["course"])
            state = new_problem_state(pid)
            problem = store.get_problem(self.db, pid)
            first_turn = True
            # Problem messages contain math, so only explicit phrases count as an attempt.
            intent = I.Intent("attempt" if I._ATTEMPT_PHRASE.search(problem_text) else "new_problem",
                              attempt=bool(I._ATTEMPT_PHRASE.search(problem_text)))
            if run_solver:
                self.solver.start(pid)

        reference = problem.get("reference_solution") if problem else None
        confidence = problem.get("confidence") if problem else None

        # Check the student's answer against the reference with real math, not the model's opinion.
        verification_note = ""
        if code_context and "[Run result" in code_context and not first_turn:
            # Real execution is the ground truth for code.
            state.attempt_shared = True
            fails = code_context.count(": FAIL")
            passes = code_context.count(": PASS")
            if passes and not fails and "killed" not in code_context.split("]")[0]:
                state.last_check = "correct"
                state.solved = True
                verification_note = f"Run result: all {passes} tests pass."
            elif fails:
                state.last_check = "incorrect"
                verification_note = f"Run result: {fails} test(s) fail. Teach how to find the bug (read the failing test, add a print, check the boundary case); don't patch it for them."
        elif intent.attempt and not first_turn and reference:
            check_text = user_text
            if images:
                transcribed = await self._transcribe(images[0], TRANSCRIBE_WORK)
                if transcribed:
                    check_text += "\n" + transcribed
                    user_text += f"\n\n[Transcription of the attached work]\n{transcribed}"
            verdict = M.matches_reference(check_text, reference.get("final_answer", ""), reference.get("final_answer_sympy", ""))
            if verdict is True:
                state.last_check = "correct"
                if confidence in ("verified", "agreed"):
                    state.solved = True
                verification_note = "Answer check (SymPy/numeric): the student's stated answer MATCHES the reference."
            elif verdict is False:
                state.last_check = "incorrect"
                verification_note = (
                    "Answer check (SymPy/numeric): the student's stated answer does NOT match the reference. "
                    "Their method may still be valid; check their steps independently and help them find where it diverges, without revealing the answer."
                )
            else:
                state.last_check = "unknown"
                verification_note = "Answer check: no final answer could be compared automatically. Check their reasoning step by step."
            if confidence == "uncertain":
                verification_note += " The reference itself is UNCERTAIN: say you're not fully sure and verify steps together."

        decision = decide(state, intent, gates, first_turn=first_turn)
        level = decision.allowed_level

        passages = await self._retrieve(course, problem["text"] if problem else user_text, user_text)
        if looked_up:
            passages.insert(0, {"label": looked_up["label"], "document_id": looked_up["document_id"], "page_index": looked_up["page_index"],
                                "printed_page": looked_up["printed_page"], "text": looked_up["text"], "kind": "textbook"})
        ref_text = _reference_text(reference, level)
        state_vars = {
            "problem": problem["text"] if problem else "",
            "attempt_status": decision.attempt_status,
            "hint_level": str(level),
            "hint_level_name": LEVEL_NAMES[level],
            "hint_instruction": decision.instruction + (("\n" + decision.special) if decision.special else ""),
            "reference_solution": ref_text,
            "solution_confidence": confidence or ("pending" if problem and problem["solver_status"] in ("pending", "running") else "none"),
            "verification_note": verification_note,
        }
        is_code = session["mode"] == "code" or (course is not None and "code" in course.kind and _looks_like_code(user_text + (code_context or "")))
        sysmsg = system_prompt(session["mode"], course, passages=passages, state_vars=state_vars, code=is_code, p=self.p, overrides=self.overrides)

        stored_user = store.add_message(self.db, sid, "user", text if text else user_text, images, {"intent": intent.as_dict(), "action": action})
        model_user_text = user_text
        if code_context:
            model_user_text += "\n\n" + code_context
        history = self._history(sid, exclude_id=stored_user)
        msgs = [{"role": "system", "content": sysmsg}, *history, _user_msg(model_user_text, images, self.p)]

        yield {
            "type": "meta",
            "intent": intent.as_dict(),
            "hint_level": level,
            "hint_level_name": LEVEL_NAMES[level],
            "reason": decision.reason,
            "problem_id": problem["id"] if problem else None,
            "solver_status": problem["solver_status"] if problem else None,
            "confidence": confidence,
            "solution_offer_pending": state.solution_offer_pending,
            "sources": [_source_meta(ps) for ps in passages],
            "user_message_id": stored_user,
        }

        checker = LeakChecker(reference, problem["text"] if problem else "") if gates.get("output_check", True) and level < 4 else None
        role = "coder" if is_code else "tutor"
        reply, stats, retries = "", {}, 0
        async for ev in self._gated_stream(role, msgs, checker, level, provider):
            if ev["type"] == "final":
                reply, stats, retries = ev["text"], ev["stats"], ev["retries"]
            else:
                yield ev

        meta = {"hint_level": level, "intent": intent.kind, "stats": stats, "leak_retries": retries, "sources": [_source_meta(ps) for ps in passages],
                "provider": provider, "model": stats.get("model")}
        mid = store.add_message(self.db, sid, "assistant", reply, None, meta)
        store.save_state(self.db, sid, state.to_dict())
        if problem:
            used = state.solution_unlocked and not state.solved
            store.update_attempt(self.db, sid, problem["id"], hint_level=level, solved=state.solved, used_full_solution=used,
                                 concept_tags=(reference or {}).get("key_concepts"))
            # Mastery moves once per problem, when it's solved or the solution is unlocked.
            if (state.solved or used) and not state.extra.get("mastery_logged"):
                from ..quiz import attempt_score, observe

                score = attempt_score(state.hint_level, state.solved, used)
                if score is not None and reference:
                    observe(self.db, session["course"], (reference or {}).get("key_concepts") or [], score)
                    state.extra["mastery_logged"] = True
                    store.save_state(self.db, sid, state.to_dict())
        yield {"type": "done", "message_id": mid, "stats": stats, "state": state.to_dict(), "leak_retries": retries}

    async def _gated_stream(self, role: str, msgs: list[dict], checker: LeakChecker | None, level: int, provider: str) -> AsyncIterator[dict]:
        """Stream sentence by sentence; a sentence is released only after the leak check passes.
        On a leak: withdraw, regenerate once with a stricter instruction, then fall back."""
        attempts = [msgs]
        for retry in range(3):
            if retry == 2:
                yield {"type": "token", "text": SAFE_FALLBACK}
                yield {"type": "final", "text": SAFE_FALLBACK, "stats": {}, "retries": retry}
                return
            full, released, leaked, stats = "", 0, None, {}
            async for c in self.models.chat(role, attempts[-1], provider=provider):
                if c.text:
                    full += c.text
                    cut = _last_boundary(full)
                    if cut > released:
                        if checker and (reason := checker.check(full[:cut], level)):
                            leaked = reason
                            break
                        yield {"type": "token", "text": full[released:cut]}
                        released = cut
                if c.done:
                    stats = {**c.stats, "model": c.stats.get("model") or self.models.last_stats.get("model")}
            if not leaked and checker:
                leaked = checker.check(full, level)
            if not leaked:
                if len(full) > released:
                    yield {"type": "token", "text": full[released:]}
                yield {"type": "final", "text": full, "stats": stats, "retries": retry}
                return
            yield {"type": "reset", "reason": f"Rephrasing (the draft {leaked})"}
            attempts.append([*attempts[0][:-1], {"role": "system", "content": stricter_instruction(leaked, level)}, attempts[0][-1]])

    # --- ask ------------------------------------------------------------------------------------

    async def _ask(self, session, course, text, images, provider) -> AsyncIterator[dict]:
        sid = session["id"]
        passages = await self._retrieve(course, text, text)
        sysmsg = system_prompt("ask", course, passages=passages, p=self.p, overrides=self.overrides)
        uid = store.add_message(self.db, sid, "user", text, images, {})
        history = self._history(sid, exclude_id=uid)
        msgs = [{"role": "system", "content": sysmsg}, *history, _user_msg(text, images, self.p)]
        yield {"type": "meta", "sources": [_source_meta(ps) for ps in passages], "user_message_id": uid}
        out, stats = [], {}
        async for c in self.models.chat("tutor", msgs, provider=provider):
            if c.text:
                out.append(c.text)
                yield {"type": "token", "text": c.text}
            if c.done:
                stats = {**c.stats, "model": self.models.last_stats.get("model")}
        reply = "".join(out)
        mid = store.add_message(self.db, sid, "assistant", reply, None, {"stats": stats, "sources": [_source_meta(ps) for ps in passages], "provider": provider})
        yield {"type": "done", "message_id": mid, "stats": stats}

    # --- helpers --------------------------------------------------------------------------------------

    async def _retrieve(self, course: Course | None, problem_text: str, query: str) -> list[dict]:
        if not self.retriever:
            return []
        try:
            q = query if len(query) > 30 else f"{problem_text}\n{query}"
            passages = await self.retriever.search(q, course=course.slug if course else None, k=4)
        except Exception:
            return []
        if self.ingest:
            # Cited textbook pages whose equations dropped out get repaired in the background.
            for ps in passages[:3]:
                if ps.get("kind") == "textbook":
                    self.ingest.request_repair(ps["document_id"], ps["page_index"])
        return passages

    async def _lookup_exercise(self, course: Course | None, text: str) -> dict | None:
        if not self.ingest or len(text) > 160:
            return None
        from ..ingest.exercises import parse_lookup

        ref = parse_lookup(text)
        if not ref:
            return None
        try:
            return await self.ingest.lookup_exercise(course.slug if course else None, ref[0], ref[1])
        except Exception:
            return None

    async def _transcribe(self, upload_id: str, instruction: str) -> str:
        b64 = store.image_b64(self.p, upload_id)
        if not b64:
            return ""
        msgs = [{"role": "user", "content": instruction, "images": [b64]}]
        out = []
        async for c in self.models.chat("vision", msgs, options={"temperature": 0}):
            out.append(c.text)
        return "".join(out).strip()

    def _history(self, sid: int, exclude_id: int | None = None) -> list[dict]:
        rows = [m for m in store.messages(self.db, sid) if m["id"] != exclude_id and m["role"] in ("user", "assistant")]
        out = []
        for m in rows[-HISTORY_MESSAGES:]:
            content = m["content"]
            if len(content) > MAX_MESSAGE_CHARS:
                content = content[:MAX_MESSAGE_CHARS] + "\n[…truncated]"
            out.append({"role": m["role"], "content": content})
        return out


def _user_msg(text: str, images: list[str], p: Paths) -> dict:
    msg = {"role": "user", "content": text}
    b64s = [b for b in (store.image_b64(p, i) for i in images) if b]
    if b64s:
        msg["images"] = b64s
    return msg


def _last_boundary(text: str) -> int:
    last = 0
    for m in _BOUNDARY.finditer(text):
        last = m.end()
    # Don't release inside an unfinished $$...$$ block.
    if text[:last].count("$$") % 2 == 1:
        last = text[:last].rfind("$$")
    return max(last, 0)


def _reference_text(ref: dict | None, level: int) -> str:
    if not ref:
        return "(not available yet: rely on careful step-by-step checking and the verification tools)"
    keep = {k: ref.get(k) for k in ("final_answer", "steps", "key_concepts", "common_mistakes") if ref.get(k)}
    return json.dumps(keep, ensure_ascii=False, indent=1)


def _source_meta(ps: dict) -> dict:
    return {k: ps.get(k) for k in ("label", "document_id", "page_index", "printed_page", "chunk_id", "section_path", "score", "kind", "text")}


def _looks_like_code(text: str) -> bool:
    return bool(re.search(r"```|\bdef |\bfun |\bval |\bclass |\bpublic static|\breturn\b|apiVersion:|FROM \w+|;\s*$", text, re.M))


def _problem_kind(course: Course | None, mode: str, text: str) -> str:
    if mode == "code":
        return "code"
    if course is None:
        return "code" if _looks_like_code(text) else "math"
    if course.is_writing:
        return "writing"
    if "code" in course.kind:
        return "code"
    if "physics" in course.kind:
        return "physics"
    if "math" in course.kind:
        return "math"
    return "general"
