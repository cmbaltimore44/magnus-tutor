"""The hint ladder and the gate state machine (pure functions, easy to test).

Levels: 0 nothing, 1 which concept applies, 2 which method or first move,
3 the specific next step, 4 full solution. At most one level per stuck turn.
Level 4 is only reached through the full-solution gate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .intent import Intent

LEVEL_NAMES = {
    0: "nothing yet",
    1: "which concept applies",
    2: "which method or first move",
    3: "the specific next step",
    4: "full solution",
}

LEVEL_INSTRUCTIONS = {
    0: "Give no hints. Do not name any law, concept, formula, or method. Ask for the student's attempt: what they tried and where they got stuck. You may clarify what the problem is asking, nothing more.",
    1: "You may point to which concept or principle applies, ideally as a guiding question. Do not name the method, set up equations, or take any step.",
    2: "You may suggest which method or first move to use. Do not carry out any step, write the setup equation in full, or give intermediate results.",
    3: "You may describe the specific next step (one step only). Do not complete later steps or state the final answer.",
    4: "The full worked solution is unlocked. Walk through it step by step, explain the reasoning, and check understanding at the end.",
}


@dataclass
class GateState:
    problem_id: int | None = None
    turns_on_problem: int = 0
    attempt_shared: bool = False
    hint_level: int = 0
    solution_offer_pending: bool = False
    solution_unlocked: bool = False
    solved: bool = False
    last_check: str | None = None  # correct | incorrect | unknown
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict | None) -> "GateState":
        d = dict(d or {})
        extra = d.pop("extra", None) or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__}, extra=extra)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    allowed_level: int
    instruction: str
    special: str = ""  # extra engine instruction for this turn (gate questions)
    attempt_status: str = ""
    reason: str = ""


def new_problem_state(problem_id: int) -> GateState:
    return GateState(problem_id=problem_id)


def decide(state: GateState, intent: Intent, gates: dict, *, first_turn: bool) -> Decision:
    """Advance the state for this turn and say what the tutor may reveal."""
    attempt_gate = gates.get("attempt_gate", True)
    ladder = gates.get("hint_ladder", True)
    solution_gate = gates.get("solution_gate", True)
    special = ""
    reason = intent.kind

    if intent.attempt:
        state.attempt_shared = True

    if intent.kind == "ask_solution" and not state.solution_unlocked:
        if solution_gate:
            state.solution_offer_pending = True
            special = (
                "The student asked for the full solution. Do NOT give it or any new hint this turn. "
                "Ask exactly one short question: would they like the full worked solution now, or after one more attempt? "
                "Briefly encourage them either way."
            )
            reason = "solution gate: asked before/after"
        else:
            state.solution_unlocked = True
    elif intent.kind == "solution_choice":
        state.solution_offer_pending = False
        if intent.solution_when == "now":
            state.solution_unlocked = True
            reason = "solution unlocked by explicit request"
        else:
            special = "The student chose to make one more attempt before seeing the solution. Encourage them and invite their next try. Do not add a new hint."
            reason = "one more attempt"
    elif intent.kind == "stuck":
        state.solution_offer_pending = False
        if first_turn and attempt_gate and not state.attempt_shared:
            reason = "attempt gate"
        elif ladder:
            state.hint_level = min(state.hint_level + 1, 3)
            reason = f"stuck: hint level {state.hint_level}"
    elif state.solution_offer_pending:
        state.solution_offer_pending = False

    if state.solution_unlocked or state.solved:
        level = 4
    elif first_turn and attempt_gate and not state.attempt_shared:
        level = 0
        special = special or (
            "This is a new problem. Before helping, ask for the student's attempt: what they've tried, and where they're stuck. "
            "Do not give any hint, method, or step yet, and do not name any law, concept, or formula (not even as a guess). "
            "You may restate what the problem asks in one sentence."
        )
        reason = "attempt gate"
    elif not ladder:
        level = 3
    else:
        level = state.hint_level

    state.turns_on_problem += 1
    status = attempt_status(state)
    return Decision(allowed_level=level, instruction=LEVEL_INSTRUCTIONS[level], special=special, attempt_status=status, reason=reason)


def attempt_status(state: GateState) -> str:
    if state.solved:
        return "Solved: the student's answer was checked and is correct."
    if not state.attempt_shared:
        return "No attempt shared yet."
    if state.last_check == "incorrect":
        return "Attempt shared; the latest answer does not match the reference. Help them find the error themselves."
    if state.last_check == "correct":
        return "Attempt shared; the latest answer matches the reference."
    return "Attempt shared; not yet checked or no final answer stated."

