"""Output check: does a reply leak the reference solution at the current hint level?

Deterministic and fast, so it can gate a streaming reply sentence by sentence:
- the final answer as text, as an equivalent expression, or as a number;
- below level 3, the later key steps of the reference, verbatim-ish.
Numbers that already appear in the problem statement never count as leaks.
"""

from __future__ import annotations

import re

from ..tools import mathcheck as M

_TRIVIAL = {0.0, 1.0, 2.0, 3.0, 4.0, 10.0, 100.0, 0.5, -1.0}


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\\(left|right|displaystyle|,|;|!|quad)", "", s)
    s = re.sub(r"[\s$\\{}()\[\]*·]", "", s)
    return s


class LeakChecker:
    def __init__(self, reference: dict | None, problem_text: str = ""):
        self.ref = reference or {}
        self.answer = str(self.ref.get("final_answer") or "").strip()
        self.answer_sympy = str(self.ref.get("final_answer_sympy") or "").strip()
        self.answer_norm = _norm(self.answer)
        self.answer_num = M.numeric_value(self.answer) if self.answer else None
        self.problem_nums = M.numbers_in(problem_text)
        steps = [str(s) for s in self.ref.get("steps") or []]
        self.late_steps = [_norm(s) for s in steps[len(steps) // 2 :] if len(_norm(s)) >= 14]
        self.problem_norm = _norm(problem_text)

    @property
    def active(self) -> bool:
        return bool(self.answer or self.answer_sympy)

    def check(self, text: str, level: int) -> str | None:
        """A short reason if `text` leaks what level `level` must not reveal."""
        if level >= 4 or not self.active or not text.strip():
            return None
        t = _norm(text)
        if len(self.answer_norm) >= 4 and self.answer_norm in t and self.answer_norm not in self.problem_norm:
            return "states the final answer"
        if self.answer_num is not None and abs(self.answer_num) not in _TRIVIAL:
            if not any(M.sig_close(n, self.answer_num, 1e-9) for n in self.problem_nums):
                for n in M.numbers_in(text):
                    if M.sig_close(n, self.answer_num, 0.005):
                        return "states the final numeric answer"
        target = self.answer_sympy or (self.answer if self.answer_num is None else "")
        if target:
            for c in M.candidate_expressions(text):
                if len(c) >= 3 and M.equivalent(c, target) is True and _norm(c) not in self.problem_norm:
                    return "states an expression equivalent to the final answer"
        if level < 3:
            for s in self.late_steps:
                if s in t:
                    return "gives a later step of the solution"
        return None


def stricter_instruction(reason: str, level: int) -> str:
    return (
        f"IMPORTANT: your previous draft {reason}, which is not allowed at hint level {level}. "
        "Rewrite the reply so it reveals nothing beyond what this level allows. Do not state the final answer, "
        "its value, or an equivalent expression. Guide with a question instead."
    )


SAFE_FALLBACK = (
    "Let's slow down and take this one piece at a time. What do you think the first thing to figure out is, "
    "and why? Tell me what you'd try and I'll react to it."
)
