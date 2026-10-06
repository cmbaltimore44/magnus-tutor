import json

from magnus_tutor import courses as C
from magnus_tutor import store
from magnus_tutor.config import load_settings
from magnus_tutor.db import get_db
from magnus_tutor.solver import SolverService, choose, parse_json
from magnus_tutor.tools.verify import run_check, verify

GOOD = {
    "final_answer": "674 N/C",
    "final_answer_sympy": "674",
    "units": "newton / coulomb",
    "steps": ["E = kq/r^2", "E = 8.99e9*3e-9/0.04 = 674 N/C"],
    "key_concepts": ["Coulomb's law"],
    "common_mistakes": [],
    "checks": [{"kind": "numeric", "lhs": "8.9875e9*3e-9/0.2**2", "rhs": "674"}],
}


def test_parse_json_handles_fences_and_latex():
    assert parse_json('```json\n{"final_answer": "x"}\n```')["final_answer"] == "x"
    assert parse_json('blah {"final_answer": "\\frac{1}{2}"} tail')["final_answer"] == "\\frac{1}{2}"
    assert parse_json("no json here") is None


def test_checks():
    assert run_check({"kind": "derivative", "lhs": "x**2*exp(-x)", "rhs": "(2*x - x**2)*exp(-x)", "var": "x"})["ok"] is True
    assert run_check({"kind": "integral", "lhs": "cos(x)", "rhs": "sin(x) + 5", "var": "x"})["ok"] is True
    assert run_check({"kind": "solve", "lhs": "x**2 - 5*x + 6 = 0", "rhs": "3", "var": "x"})["ok"] is True
    assert run_check({"kind": "solve", "lhs": "x**2 - 5*x + 6 = 0", "rhs": "4", "var": "x"})["ok"] is False
    assert run_check({"kind": "numeric", "lhs": "__import__('os').system('echo hi')", "rhs": "0"})["ok"] is None
    assert run_check({"kind": "units", "lhs": "volt / meter", "rhs": "newton / coulomb"})["ok"] is True


def test_verify_requires_the_final_answer_to_be_recomputed():
    assert verify(GOOD, "physics")["status"] == "verified"
    side = {**GOOD, "checks": [{"kind": "numeric", "lhs": "2+2", "rhs": "4"}]}
    assert verify(side, "physics")["status"] == "unverified"
    wrong = {**GOOD, "checks": [{"kind": "numeric", "lhs": "8.9875e9*3e-9/0.2", "rhs": "674"}]}
    assert verify(wrong, "physics")["status"] == "failed"


def test_choose_confidence():
    v_ok = {"status": "verified"}
    v_un = {"status": "unverified"}
    other = {**GOOD, "final_answer": "135 N/C", "final_answer_sympy": "135"}
    assert choose([GOOD], [v_ok])[1] == "verified"
    assert choose([GOOD, GOOD], [v_un, v_un])[1] == "agreed"
    assert choose([GOOD, other], [v_un, v_un])[1] == "uncertain"
    assert choose([GOOD], [v_un])[1] == "uncertain"


async def test_solver_service_stores_reference(p, fake_models):
    mm, fp = fake_models
    fp.reply = lambda messages, **kw: json.dumps(GOOD)
    course = C.create_course("E&M", kind=["physics"], p=p)
    db = get_db(p)
    pid = store.create_problem(db, course.slug, "A 3 nC charge, r = 0.2 m. Find E.", kind="physics")
    events = []
    svc = SolverService(p, db, mm, lambda: load_settings(p), publish=lambda k, d: events.append(d))
    res = await svc.solve(pid)
    pr = store.get_problem(db, pid)
    assert pr["solver_status"] == "done" and pr["confidence"] == "verified"
    assert pr["reference_solution"]["final_answer"] == "674 N/C"
    assert res["runs"] == 1  # verified, so no extra run
    assert events[-1]["status"] == "done"
    assert fp.calls[0]["think"] is True


async def test_unverified_gets_a_second_run(p, fake_models):
    mm, fp = fake_models
    unverifiable = {**GOOD, "checks": []}
    fp.reply = lambda messages, **kw: json.dumps(unverifiable)
    db = get_db(p)
    pid = store.create_problem(db, None, "Explain something", kind="math")
    svc = SolverService(p, db, mm, lambda: load_settings(p))
    res = await svc.solve(pid)
    assert res["runs"] == 2 and res["confidence"] == "agreed"
