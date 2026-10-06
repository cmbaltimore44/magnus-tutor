"""Math helpers shared by answer checking, the leak check, and verification.

Everything is defensive: model and student text is messy, so parsing failures
return None instead of raising, and comparisons are numeric (random test
values) with a symbolic fallback, which is fast and robust to equivalent forms.
"""

from __future__ import annotations

import math
import random
import re
from functools import lru_cache

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
# Names SymPy would otherwise read as built-ins (Q assumptions, E Euler's number, N(), S, gamma()...).
# In physics they are variables. I stays the imaginary unit and pi stays pi.
_LOCALS = {n: sp.Symbol(n) for n in ("Q", "E", "N", "S", "O", "C", "beta", "gamma", "zeta", "Lambda", "lamda", "B", "U", "V", "W", "F", "L", "R", "T")}
MAX_LEN = 240

_LATEX_CLEAN = [
    (r"\\left|\\right", ""),
    (r"\\varepsilon", r"\\epsilon"),
    (r"\\[,;!:]|\\quad|\\qquad|~", " "),
    (r"\\cdot|\\times", r" \\cdot "),
    (r"\\dfrac|\\tfrac", r"\\frac"),
    (r"\\mathrm\{[^}]*\}|\\text\{[^}]*\}|\\,\\mathrm\{[^}]*\}", ""),  # units / words
    (r"\\boxed\{(.*)\}", r"\1"),
    (r"\\approx", "="),
]

_UNIT_WORDS = re.compile(
    r"\b(N/C|V/m|N|C|V|J|W|T|A|Hz|eV|keV|MeV|kg|g|m/s\^?2?|m/s|m|cm|mm|nm|km|s|ms|ns|Pa|F|H|Ohm|ohm|Ω|K|mol|rad)\b\.?$"
)


def _clean_latex(s: str) -> str:
    for pat, rep in _LATEX_CLEAN:
        s = re.sub(pat, rep, s)
    return s.strip().strip("$").strip()


_ALIASES = {"lambda": "lamda", "eps0": "epsilon_0", "epsilon0": "epsilon_0", "e0": "epsilon_0", "varepsilon_0": "epsilon_0",
            "epsilon_0": "epsilon_0", "varepsilon": "epsilon", "mu0": "mu_0", "mu_0": "mu_0", "hbar": "hbar"}


def _sym_name(name: str) -> str:
    """Unify spellings (braces, backslashes, ε₀ aliases) but keep case: R and r differ."""
    n = re.sub(r"[{}\\]", "", name)
    return _ALIASES.get(n.lower(), n)


@lru_cache(maxsize=2048)
def to_sympy(text: str):
    """Parse plain SymPy syntax or LaTeX into a SymPy expression, or None."""
    if not text:
        return None
    s = text.strip().strip("$").strip().rstrip(".")
    if not s or len(s) > MAX_LEN:
        return None
    s = _UNIT_WORDS.sub("", s).strip()
    if not s:
        return None
    if "\\" in s or "{" in s:
        try:
            from sympy.parsing.latex import parse_latex

            return parse_latex(_clean_latex(s))
        except Exception:
            return None
    try:
        plain = s.replace("×", "*").replace("·", "*").replace("−", "-")
        plain = re.sub(r"\blambda\b", "lamda", plain)
        plain = re.sub(r"(\d)\s*[x*]\s*10\s*\^\s*\(?(-?\d+)\)?", r"\1e\2", plain)
        return parse_expr(plain, local_dict=dict(_LOCALS), transformations=_TRANSFORMS, evaluate=True)
    except Exception:
        return None


def _rename(expr):
    """Unify symbol spellings: epsilon_{0} == epsilon_0 == Epsilon_0."""
    special = {"pi": sp.pi, "i": sp.I}  # a bare i in physics answers is the imaginary unit
    mapping = {s: special.get(s.name, sp.Symbol(_sym_name(s.name), positive=True)) for s in expr.free_symbols}
    return expr.xreplace(mapping)


def equivalent(a, b, rel_tol: float = 1e-6, trials: int = 6) -> bool | None:
    """True/False if the two expressions (strings or SymPy) agree; None if unknown."""
    ea = to_sympy(a) if isinstance(a, str) else a
    eb = to_sympy(b) if isinstance(b, str) else b
    if ea is None or eb is None:
        return None
    try:
        if isinstance(ea, sp.Equality) or isinstance(eb, sp.Equality):
            return None
        ea, eb = _rename(sp.sympify(ea)), _rename(sp.sympify(eb))
        syms = sorted(ea.free_symbols | eb.free_symbols, key=lambda s: s.name)
        rng = random.Random(12345)
        agree = 0
        for _ in range(trials):
            vals = {s: rng.uniform(0.5, 3.0) for s in syms}
            va, vb = complex(ea.evalf(subs=vals)), complex(eb.evalf(subs=vals))
            if not (math.isfinite(abs(va)) and math.isfinite(abs(vb))):
                continue
            scale = max(abs(va), abs(vb), 1e-300)
            if abs(va - vb) / scale > rel_tol:
                return False
            agree += 1
        return True if agree else None
    except Exception:
        return None


def numeric_value(text: str) -> float | None:
    e = to_sympy(text)
    if e is None:
        return None
    try:
        e = sp.sympify(e)
        if e.free_symbols - {sp.Symbol("pi"), sp.Symbol("e")}:
            return None
        v = complex(e.evalf())
        return v.real if abs(v.imag) < 1e-12 else None
    except Exception:
        return None


_NUM = re.compile(
    r"(?<![\w.])[-−]?\d+(?:[.,]\d+)?(?:\s*(?:\\times|×|x|\*|\\cdot)\s*10\s*\^\s*\{?\s*[-−]?\d+\s*\}?|e[-+]?\d+)?"
)


def numbers_in(text: str) -> list[float]:
    """Every number in free text, understanding 2.3e5, 2.3 × 10^5 and 2.3\\times10^{5}."""
    out = []
    for m in _NUM.finditer(text):
        tok = m.group(0).replace("−", "-").replace(",", ".")
        mm = re.match(r"(-?\d+(?:\.\d+)?)\s*(?:\\times|×|x|\*|\\cdot)\s*10\s*\^\s*\{?\s*(-?\d+)", tok)
        try:
            out.append(float(f"{mm.group(1)}e{mm.group(2)}") if mm else float(tok))
        except ValueError:
            pass
    return out


def sig_close(a: float, b: float, rel: float = 0.01) -> bool:
    if a == b:
        return True
    return abs(a - b) <= rel * max(abs(a), abs(b))


_MATH_SEG = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)


def math_segments(text: str) -> list[str]:
    segs = [next(g for g in m.groups() if g) for m in _MATH_SEG.finditer(text)]
    return segs


def candidate_expressions(text: str) -> list[str]:
    """Expressions a reply or attempt might state as results: right-hand sides of
    '=' in math segments and plain lines, \\boxed{...}, and 'answer is X'."""
    cands: list[str] = []
    for seg in math_segments(text) + [ln for ln in text.splitlines() if "=" in ln and "$" not in ln]:
        boxed = re.findall(r"\\boxed\{(.+)\}", seg)
        cands += boxed
        parts = re.split(r"(?<![<>!])=|\\approx|≈", seg)
        if len(parts) > 1:
            cands += [x.strip() for x in parts[1:] if x.strip()]
        elif seg.strip():
            cands.append(seg.strip())
    for m in re.finditer(r"(?:answer|result)\s+(?:is|=|:)\s*([^\n;]+?)(?:\.\s|\.?$|\n)", text, re.I | re.M):
        cands.append(m.group(1).strip())
    seen, out = set(), []
    for c in cands:
        c = c.strip().rstrip(",.")
        if c and c not in seen and len(c) <= MAX_LEN:
            seen.add(c)
            out.append(c)
    return out


def final_answer_from_attempt(text: str) -> str | None:
    """The student's most likely final answer: the last stated result."""
    cands = candidate_expressions(text)
    return cands[-1] if cands else None


def matches_reference(text: str, ref_answer: str, ref_sympy: str = "") -> bool | None:
    """Does any stated result in `text` match the reference answer?"""
    target = ref_sympy or ref_answer
    ref_num = numeric_value(ref_answer) if ref_answer else None
    saw_any = False
    for c in reversed(candidate_expressions(text)):
        r = equivalent(c, target) if target else None
        if r is None and ref_num is not None:
            v = numeric_value(c)
            r = None if v is None else sig_close(v, ref_num)
        if r is True:
            return True
        if r is False:
            saw_any = True
    if ref_num is not None:
        nums = numbers_in(text)
        if any(sig_close(n, ref_num) for n in nums):
            return True
        if nums:
            saw_any = True
    return False if saw_any else None
