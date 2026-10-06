"""Verify a solver's reference solution before the tutor trusts it.

Math:    the solver's declarative checks, evaluated with SymPy (no code execution):
         sympy_equal, derivative, integral (up to a constant), solve, numeric, limit.
Physics: dimensional analysis of the final expression with pint, using the
         solver's symbol → unit map, plus a numeric sanity check.
Code:    the reference program runs in the sandbox against the tests.
Every expression is screened before parsing (no dunder, imports, lambdas...).
"""

from __future__ import annotations

import math
import re

import sympy as sp

from . import mathcheck as M

_FORBIDDEN = re.compile(r"__|\bimport\b|\blambda\b|\bexec\b|\beval\b|\bopen\b|\bos\.|\bsys\.|;|\bgetattr\b|\bglobals\b|\blocals\b|`")


def _safe(expr: str) -> bool:
    return isinstance(expr, str) and 0 < len(expr) <= M.MAX_LEN and not _FORBIDDEN.search(expr)


def _parse(expr: str):
    return M.to_sympy(expr) if _safe(expr) else None


def _sym(name: str):
    return sp.Symbol(M._sym_name(name or "x"), positive=True)


def run_check(c: dict) -> dict:
    kind = c.get("kind", "")
    lhs, rhs, var = str(c.get("lhs", "")), str(c.get("rhs", "")), str(c.get("var", "") or "x")
    out = {"kind": kind, "lhs": lhs, "rhs": rhs, "ok": None, "detail": ""}
    try:
        if kind in ("sympy_equal", "numeric"):
            a, b = _parse(lhs), _parse(rhs)
            numeric = a is not None and b is not None and not (sp.sympify(a).free_symbols or sp.sympify(b).free_symbols)
            # Pure numbers are compared like measurements (models round); expressions exactly.
            out["ok"] = M.equivalent(a, b, rel_tol=5e-3 if (numeric or kind == "numeric") else 1e-6)
        elif kind == "derivative":
            f, g = _parse(lhs), _parse(rhs)
            if f is not None and g is not None:
                f = M._rename(sp.sympify(f))
                out["ok"] = M.equivalent(sp.diff(f, _sym(var)), M._rename(sp.sympify(g)))
        elif kind == "integral":
            f, F = _parse(lhs), _parse(rhs)
            if f is not None and F is not None:
                F = M._rename(sp.sympify(F))
                out["ok"] = M.equivalent(sp.diff(F, _sym(var)), M._rename(sp.sympify(f)))
        elif kind == "solve":
            eq_l, _, eq_r = lhs.partition("=")
            L, R, ans = _parse(eq_l), _parse(eq_r or "0"), _parse(rhs)
            if L is not None and R is not None and ans is not None:
                expr = M._rename(sp.sympify(L - R))
                ans = M._rename(sp.sympify(ans))
                # Substitute the claimed solution: the equation must hold.
                residual = expr.subs(_sym(var), ans)
                out["ok"] = M.equivalent(residual, sp.Integer(0)) if residual.free_symbols else bool(abs(complex(residual.evalf())) < 1e-6)
        elif kind == "limit":
            f, val = _parse(lhs), _parse(rhs)
            point = c.get("point", "oo")
            if f is not None and val is not None:
                f = M._rename(sp.sympify(f))
                pt = sp.oo if str(point) in ("oo", "inf", "infinity") else sp.sympify(point)
                out["ok"] = M.equivalent(sp.limit(f, _sym(var), pt), M._rename(sp.sympify(val)))
        elif kind == "units":
            out["ok"] = units_match(lhs, rhs)
        else:
            out["detail"] = "unknown check kind"
    except Exception as e:  # malformed model output must never crash the pipeline
        out["ok"] = None
        out["detail"] = f"{type(e).__name__}"
    return out


# --- physics: units --------------------------------------------------------------------------------

_UREG = None


def ureg():
    global _UREG
    if _UREG is None:
        import pint

        _UREG = pint.UnitRegistry()
        _UREG.define("epsilon_0_unit = farad / meter")
    return _UREG


def units_match(a: str, b: str) -> bool | None:
    try:
        ua, ub = ureg().parse_expression(a), ureg().parse_expression(b)
        return ua.dimensionality == ub.dimensionality
    except Exception:
        return None


def dimension_of(expr_text: str, symbol_units: dict[str, str]):
    """Dimensionality of a symbolic answer given each symbol's unit, e.g.
    q/(4*pi*epsilon_0*r**2) with q: coulomb, epsilon_0: farad/meter, r: meter → [mass]·[length]/[current]/[time]^3."""
    e = _parse(expr_text)
    if e is None:
        return None
    try:
        u = ureg()
        e = M._rename(sp.sympify(e))
        units = {M._sym_name(k): v for k, v in (symbol_units or {}).items()}
        subs = {}
        for s in e.free_symbols:
            unit = units.get(s.name)
            if unit is None:
                return None
            subs[s] = u.parse_expression(unit) if unit not in ("1", "dimensionless", "") else 1
        # Evaluate the expression tree with pint quantities.
        val = _eval_pint(e, subs)
        return val.dimensionality if hasattr(val, "dimensionality") else ureg().dimensionless.dimensionality
    except Exception:
        return None


def _eval_pint(e, subs):
    if e in subs:
        return subs[e]
    if e.is_Number or e.is_NumberSymbol:
        return float(e)
    if isinstance(e, sp.Add):
        terms = [_eval_pint(a, subs) for a in e.args]
        total = terms[0]
        for t in terms[1:]:
            total = total + t
        return total
    if isinstance(e, sp.Mul):
        out = 1
        for a in e.args:
            out = out * _eval_pint(a, subs)
        return out
    if isinstance(e, sp.Pow):
        base = _eval_pint(e.args[0], subs)
        return base ** float(e.args[1])
    if isinstance(e, sp.Function):
        arg = _eval_pint(e.args[0], subs)
        if hasattr(arg, "dimensionality") and arg.dimensionality:
            raise ValueError("function of a dimensional quantity")
        return 1.0
    raise ValueError(f"can't evaluate {type(e).__name__}")


def physics_checks(ref: dict) -> list[dict]:
    out = []
    target_units = (ref.get("units") or "").strip()
    expr = (ref.get("final_answer_sympy") or "").strip()
    sym_units = ref.get("symbol_units") or {}
    if target_units and expr and sym_units:
        dim = dimension_of(expr, sym_units)
        try:
            want = ureg().parse_expression(target_units).dimensionality
        except Exception:
            want = None
        ok = None if dim is None or want is None else dim == want
        out.append({"kind": "dimensions", "lhs": expr, "rhs": target_units, "ok": ok, "detail": f"{dim} vs {want}"})
    num = M.numeric_value(ref.get("final_answer", "")) if ref.get("final_answer") else None
    if num is not None:
        out.append({"kind": "sanity", "lhs": ref.get("final_answer"), "rhs": "finite", "ok": math.isfinite(num), "detail": ""})
    return out


# --- code ------------------------------------------------------------------------------------------------


def code_checks(ref: dict, p=None) -> list[dict]:
    code, lang, tests = ref.get("reference_code"), ref.get("language"), ref.get("tests") or []
    if not code or not lang or not tests:
        return []
    from ..languages import load
    from ..sandbox import run

    langs = load(p)
    if lang not in langs:
        return [{"kind": "code", "ok": None, "detail": f"unknown language {lang}"}]
    ext = (langs[lang].get("extensions") or [".txt"])[0]
    fname = langs[lang].get("filename") or f"main{ext}"
    norm = [{"name": t.get("name", f"t{i}"), "stdin": t.get("stdin", ""), "expected": t.get("expected", "")} for i, t in enumerate(tests) if isinstance(t, dict)]
    r = run({fname: code}, lang, tests=norm, limits={"timeout_s": 8, "cpu_s": 8}, p=p)
    return [{"kind": "code", "ok": r.ok, "detail": f"{sum(t['passed'] for t in r.tests)}/{len(r.tests)} tests passed"}]


# --- the whole verification ---------------------------------------------------------------------------------


def _recomputes_final(c: dict, ref: dict) -> bool:
    if c["kind"] == "code":
        return True
    if c["kind"] not in ("numeric", "sympy_equal", "solve", "integral", "derivative", "limit"):
        return False
    final_expr = ref.get("final_answer_sympy") or ""
    final_num = M.numeric_value(ref.get("final_answer") or "") if ref.get("final_answer") else None
    for side in (c.get("rhs", ""), c.get("lhs", "")):
        if final_expr and M.equivalent(side, final_expr, rel_tol=5e-3) is True:
            return True
        if final_num is not None:
            v = M.numeric_value(side)
            if v is not None and M.sig_close(v, final_num, 5e-3):
                return True
    return False


def _has_numeric_or_symbolic_link(passed: list[dict], ref: dict) -> bool:
    return any(c.get("recomputes_final") for c in passed) or any(c["kind"] == "code" for c in passed)


def verify(ref: dict, kind: str, p=None) -> dict:
    checks = [run_check(c) for c in (ref.get("checks") or [])[:12] if isinstance(c, dict)]
    if kind == "physics":
        checks += physics_checks(ref)
    if kind == "code":
        checks += code_checks(ref, p)
    passed = [c for c in checks if c["ok"] is True]
    failed = [c for c in checks if c["ok"] is False]
    for c in passed:
        c["recomputes_final"] = _recomputes_final(c, ref)
    # "verified" needs a passing check that reaches the final answer itself (or code tests),
    # not just a true side calculation.
    substantive = [c for c in passed if c.get("recomputes_final") or c["kind"] in ("code", "dimensions") and _has_numeric_or_symbolic_link(passed, ref)]
    status = "failed" if failed else ("verified" if substantive else "unverified")
    return {
        "status": status,
        "passed": len(passed),
        "failed": len(failed),
        "inconclusive": len(checks) - len(passed) - len(failed),
        "checks": checks,
        "summary": f"{len(passed)} passed, {len(failed)} failed, {len(checks) - len(passed) - len(failed)} inconclusive",
    }
