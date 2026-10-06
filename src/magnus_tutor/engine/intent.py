"""What is the student doing this turn? Deterministic rules, so the gates are
enforced in code (local models follow instructions less reliably)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ASK_SOLUTION = re.compile(
    r"\b(show|give|tell|walk)\s+(me\s+)?(the\s+|a\s+)?(full\s+|whole\s+|complete\s+|worked\s+|entire\s+)?(solution|answer|work(ed)?\s*(out|solution)?)\b"
    r"|\bjust\s+(tell|give|show)\s+me\b|\bsolve\s+(it|this)(\s+for\s+me)?\b|\bwhat('?s| is)\s+the\s+(final\s+)?answer\b"
    r"|\bi\s+give\s+up\b|\bdo\s+it\s+for\s+me\b|\bunlock\b",
    re.I,
)
_STUCK = re.compile(
    r"\b(stuck|no\s+idea|(i\s+)?(don'?t|do\s+not)\s+(know|get|understand|see)|not\s+sure\s+(how|where|what|why)|hint|lost|confused|can'?t\s+figure"
    r"|where\s+(do|should)\s+i\s+(start|begin)|what('?s| is)\s+the\s+(first|next)\s+step|how\s+do\s+i\s+(start|begin|approach)|help(\s+me)?$|help\s+me)\b",
    re.I,
)
_ATTEMPT_PHRASE = re.compile(
    r"\b(i\s+(got|get|tried|think|used|wrote|found|set|did|have|started|computed|calculated|integrated|differentiated|plugged)|my\s+(answer|attempt|work|approach|code|result|draft|thesis)"
    r"|so\s+far|first\s+i|then\s+i|so\s+it'?s|which\s+gives|that\s+gives|here'?s\s+(my|what))\b",
    re.I,
)
_MATHY = re.compile(r"=|\\frac|\\int|\^|\d\s*[*/+\-]\s*\d|\$|\bdef\b|\bfun\b|\bval\b|```|\breturn\b")
# "Problem 3:" starts a new problem; "Problem 6.12" (a textbook lookup) must not be cut at the dot.
_NEW_PROBLEM = re.compile(r"^\s*(new|next|another)\s+(problem|question|exercise)\b|^\s*(problem|exercise|question)\s+\d+[a-z]?\s*[:.)](?!\d)", re.I)
_SOLUTION_NOW = re.compile(r"\b(now|before|show|yes|yeah|yep|sure|go\s+ahead|please|just\s+show)\b", re.I)
_SOLUTION_AFTER = re.compile(r"\b(after|one\s+more|try\s+again|let\s+me\s+try|i'?ll\s+try|not\s+(now|yet)|wait|no|nope|later)\b", re.I)


@dataclass
class Intent:
    kind: str  # ask_solution | stuck | attempt | question | new_problem | solution_choice
    attempt: bool = False  # message contains the student's own work
    stuck: bool = False
    solution_when: str | None = None  # now | after (answer to the gate question)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "attempt": self.attempt, "stuck": self.stuck, "solution_when": self.solution_when}


def has_attempt(text: str, has_images: bool = False) -> bool:
    if has_images:
        return True
    t = text.strip()
    if _ATTEMPT_PHRASE.search(t):
        return True
    return bool(_MATHY.search(t)) and len(t) >= 12


def classify(text: str, *, has_images: bool = False, solution_offer_pending: bool = False) -> Intent:
    t = text.strip()
    if solution_offer_pending:
        # Negations first: "not now", "no, let me try" must never unlock the solution.
        if _SOLUTION_AFTER.search(t):
            return Intent("solution_choice", attempt=has_attempt(t, has_images), solution_when="after")
        # A short affirmative ("now", "yes please", "show it") or an explicit request unlocks;
        # longer messages that merely contain "now" ("ok so now I have v = 3t") are attempts.
        short_yes = len(t.split()) <= 6 and _SOLUTION_NOW.search(t) and not has_attempt(t, has_images)
        if short_yes or _ASK_SOLUTION.search(t):
            return Intent("solution_choice", solution_when="now")
    if _NEW_PROBLEM.search(t):
        return Intent("new_problem")
    attempt = has_attempt(t, has_images)
    if _ASK_SOLUTION.search(t):
        return Intent("ask_solution", attempt=attempt)
    stuck = bool(_STUCK.search(t))
    if stuck:
        return Intent("stuck", attempt=attempt, stuck=True)
    if attempt:
        return Intent("attempt", attempt=True)
    return Intent("question")
