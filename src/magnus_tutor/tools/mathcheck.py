"""Math helpers shared by answer checking, the leak check, and verification.

Everything is defensive: model and student text is messy, so parsing failures
return None instead of raising, and comparisons are numeric (random test
values) with a symbolic fallback, which is fast and robust to equivalent forms.
"""

from __future__ import annotations

import ast
import math
import random
import re
from functools import lru_cache

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    standard_transformations,
    stringify_expr,
)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
# Names SymPy would otherwise read as built-ins (Q assumptions, E Euler's number, N(), S, gamma()...).
# In physics they are variables. I stays the imaginary unit and pi stays pi.
_LOCALS = {n: sp.Symbol(n) for n in ("Q", "E", "N", "S", "O", "C", "beta", "gamma", "zeta", "Lambda", "lamda", "B", "U", "V", "W", "F", "L", "R", "T")}

# --- safe parsing ----------------------------------------------------------------------------
# Student and model text must never reach eval(). SymPy's tokenizer turns the text into Python
# source (without running it); that source is checked against a strict allowlist (numbers,
# + - * / **, a fixed set of functions, plain symbol names; no attributes, subscripts, keywords,
# lambdas or dunder names) and only then evaluated with no builtins.

_FUNCS = {n: getattr(sp, n) for n in ("sqrt", "exp", "log", "sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan",
                                      "sinh", "cosh", "tanh", "Abs")}
_FUNCS["ln"] = sp.log
_CONSTS = {"pi": sp.pi, "I": sp.I, "oo": sp.oo}
_CONSTRUCTORS = {"Symbol": sp.Symbol, "Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational}
_NAMESPACE = {**_FUNCS, **_CONSTS, **_CONSTRUCTORS}
_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)
_SYMBOL_ARG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
_NUMBER_ARG = re.compile(r"^[0-9][0-9.]*(?:[eE][-+]?[0-9]{1,3})?$")
MAX_EXPONENT = 64


class UnsafeExpression(ValueError):
    pass


def _number(node) -> float | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("Integer", "Float") and node.args:
        a = node.args[0]
        if isinstance(a, ast.Constant):
            try:
                return float(a.value)
            except (TypeError, ValueError):
                return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _number(node.operand)
        return -v if v is not None else None
    return None


def _check(node) -> None:
    if isinstance(node, ast.Expression):
        return _check(node.body)
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _OPS):
            raise UnsafeExpression(type(node.op).__name__)
        if isinstance(node.op, ast.Pow):
            exp = _number(node.right)
            # 9**9**9 would hang the server: exponents must be small constants or symbolic.
            if isinstance(node.right, ast.BinOp) and isinstance(node.right.op, ast.Pow) or (exp is not None and abs(exp) > MAX_EXPONENT):
                raise UnsafeExpression("exponent too large")
        _check(node.left)
        _check(node.right)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _OPS):
            raise UnsafeExpression(type(node.op).__name__)
        return _check(node.operand)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords or node.func.id not in _NAMESPACE or node.func.id in _CONSTS:
            raise UnsafeExpression("call")
        name = node.func.id
        if name in _CONSTRUCTORS:
            if len(node.args) not in (1, 2) or not all(isinstance(a, ast.Constant) for a in node.args):
                raise UnsafeExpression("constructor")
            for a in node.args:
                v = a.value
                if isinstance(v, bool) or not isinstance(v, (int, str)):
                    raise UnsafeExpression("constant")
                if isinstance(v, str) and not (_SYMBOL_ARG.match(v) if name == "Symbol" else _NUMBER_ARG.match(v)):
                    raise UnsafeExpression("constant")
            return
        if len(node.args) != 1:
            raise UnsafeExpression("arity")
        return _check(node.args[0])
    if isinstance(node, ast.Name):
        if node.id.startswith("_") or node.id not in _NAMESPACE:
            raise UnsafeExpression(node.id)
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return
    raise UnsafeExpression(type(node).__name__)


def safe_parse(text: str):
    """Plain math text → SymPy expression, without ever evaluating anything outside the allowlist."""
    code = stringify_expr(text, dict(_LOCALS), dict(_NAMESPACE), _TRANSFORMS)
    tree = ast.parse(code, mode="eval")

    class _LocalsAsConstants(ast.NodeTransformer):
        # Physics letters (Q, E, R, ...) appear as bare names bound to Symbols: fine to reference.
        def visit_Name(self, node):
            return ast.Constant(0) if node.id in _LOCALS else node

    _check(_LocalsAsConstants().visit(ast.parse(code, mode="eval")))
    return eval(compile(tree, "<math>", "eval"), {"__builtins__": {}}, {**_NAMESPACE, **_LOCALS})  # noqa: S307 - allowlisted AST, no builtins
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

# Units are only stripped after a number ("674 N/C"), never from a symbolic answer ("F/m", "m*g").
_UNIT_WORDS = re.compile(
    r"(?<=[0-9.])\s*(N/C|V/m|N|C|V|J|W|T|A|Hz|eV|keV|MeV|kg|g|m/s\^?2?|m/s|m|cm|mm|nm|km|s|ms|ns|Pa|F|H|Ohm|ohm|Ω|K|mol|rad)\.?$"
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
        return safe_parse(plain)
    except Exception:
        return None


def _rename(expr):
    """Unify symbol spellings: epsilon_{0} == epsilon_0 == Epsilon_0."""
    # A bare i is the imaginary unit and a bare e is Euler's number (e^(-x) == exp(-x)); an
    # elementary-charge e on both sides still compares equal.
    special = {"pi": sp.pi, "i": sp.I, "e": sp.E}
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
            # Relative tolerance, plus an absolute floor so float noise around 0 isn't a mismatch.
            if abs(va - vb) > rel_tol * max(abs(va), abs(vb)) + 1e-9:
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
        e = _rename(e)
        if e.free_symbols:
            return None
        v = complex(e.evalf())
        return v.real if abs(v.imag) < 1e-12 else None
    except Exception:
        return None


_NUM = re.compile(
    r"(?<![\w.])[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:\\times|×|x|\*|\\cdot)\s*10\s*\^\s*\{?\s*[-−]?\d+\s*\}?|e[-+]?\d+)?"
)


def numbers_in(text: str) -> list[float]:
    """Every number in free text, understanding 2.3e5, 2.3 × 10^5, 2.3\\times10^{5} and 1,500."""
    out = []
    for m in _NUM.finditer(text):
        tok = m.group(0).replace("−", "-").replace(",", "")  # commas only ever group thousands here
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
    """Does the student's FINAL stated answer match the reference? Only the last stated
    result counts (an intermediate value or a list of guesses doesn't); with no stated
    result, a single number in the text is taken as the answer. None = can't tell."""
    target = ref_sympy or ref_answer
    ref_num = numeric_value(ref_answer) if ref_answer else None
    final = final_answer_from_attempt(text)
    if final is None:
        nums = numbers_in(text)
        if len(nums) != 1 or ref_num is None:
            return None
        return sig_close(nums[0], ref_num)
    if re.search(r"\bor\b", final):
        return None  # "3 or 7 or 12" is a guess, not an answer
    if ref_num is not None:
        # Numbers are compared like measurements (1%), whatever units or rounding.
        v = numeric_value(final)
        if v is None:
            nums = numbers_in(final)
            v = nums[0] if len(nums) == 1 else None
        if v is not None:
            return sig_close(v, ref_num)
    return equivalent(final, target) if target else None
