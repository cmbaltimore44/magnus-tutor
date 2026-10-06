"""Benchmarks: accuracy per model and role on bench/problems.yaml, latency,
memory, and how a tutor turn fares while the hidden solver runs.

  tutor bench run --models qwen3.5:9b gemma4:12b --roles tutor solver
  tutor bench latency --models ...
  tutor bench concurrency
  tutor bench report

Results go to bench/results/*.json and bench/results/REPORT.md.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
from pathlib import Path

import psutil
import yaml

from . import runtime
from .config import load_settings, paths
from .llm.base import ProviderError
from .llm.manager import ModelManager, Preempted
from .solver import SCHEMA, SolverService, parse_json
from .tools import mathcheck as M
from .tools.verify import verify

ROOT = Path(__file__).resolve().parents[2] / "bench"
RESULTS = ROOT / "results"
GB = 1024**3

SOLVE_PROMPT = """Solve this {course_title} problem. Return JSON only with keys:
"final_answer" (short string with units if physical), "final_answer_sympy" (SymPy expression of the answer using the problem's symbols, or "" if not mathematical),
"units", "symbol_units", "steps", "key_concepts", "common_mistakes", "checks" (computer-algebra checks: objects with kind numeric|sympy_equal|derivative|integral|solve, lhs, rhs, var).

Problem: {text}"""

CODE_PROMPT = """{text}

Reply with only the code in one fenced code block (```{language} ... ```). Do not include example usage, tests, or a main program."""

CONFIG_PROMPT = """{text}

Reply with only the file contents in one fenced code block."""

COURSE_TITLES = {"em": "Electricity and Magnetism", "qm": "Introduction to Quantum Physics", "psl": "Programming Systems and Languages (SML)", "cloud": "Cloud Computing"}


def load_problems() -> list[dict]:
    return yaml.safe_load((ROOT / "problems.yaml").read_text())


# --- grading ------------------------------------------------------------------------------


def _quantity_value(text: str, units: str) -> float | None:
    try:
        from .tools.verify import ureg

        q = ureg().parse_expression(text.replace("×", "*").replace("μ", "u").replace("Ω", "ohm"))
        if hasattr(q, "to"):
            return float(q.to(units.replace("μ", "u")).magnitude)
    except Exception:
        return None
    return None


def grade_answer(problem: dict, ref: dict | None) -> bool:
    if not ref:
        return False
    g = problem["grader"]
    fa, fs = str(ref.get("final_answer", "")), str(ref.get("final_answer_sympy", ""))
    if g == "numeric":
        want = float(problem["answer"])
        tol = float(problem.get("rel_tol", 0.01))
        vals = []
        v = _quantity_value(fa, problem.get("units", "")) if problem.get("units") else None
        if v is not None:
            vals.append(v)
        for cand in (fs, fa):
            n = M.numeric_value(cand) if cand else None
            if n is not None:
                vals.append(n)
        vals += M.numbers_in(fa)[:2]
        return any(M.sig_close(x, want, tol) for x in vals)
    if g == "symbolic":
        for cand in (fs, fa):
            if cand and M.equivalent(cand, problem["answer_sympy"], rel_tol=1e-4) is True:
                return True
        return any(M.equivalent(c, problem["answer_sympy"], rel_tol=1e-4) is True for c in M.candidate_expressions(fa))
    if g == "set":
        want = sorted(float(x) for x in str(problem["answer"]).split(","))
        got = sorted(set(M.numbers_in(fa + " " + fs)))
        return got == want
    return False


def extract_code(text: str) -> str:
    blocks = re.findall(r"```[\w+-]*\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text.strip()


def grade_code(problem: dict, code: str, p) -> tuple[bool, str]:
    from .sandbox import run

    lang = problem["language"]
    if lang == "java":
        main = problem["driver_java_main"]
        body = code.strip()
        if "class Main" in body:
            idx = body.rfind("}")
            src = body[:idx] + "\n" + main + "\n}\n"
        else:
            src = "public class Main {\n" + body + "\n" + main + "\n}\n"
        r = run({"Main.java": src}, "java", p=p, limits={"timeout_s": 15, "cpu_s": 15})
    else:
        fname = "main.sml" if lang == "sml" else "main.py"
        r = run({fname: code + "\n" + problem["driver"]}, lang, p=p, limits={"timeout_s": 15, "cpu_s": 15})
    out = r.stdout.strip()
    if lang == "sml":
        out = "\n".join(ln for ln in out.splitlines() if not ln.startswith(("val ", "datatype ", "type ")))
    ok = out == str(problem["expected"]).strip()
    return ok, (out if ok else (r.stderr or out)[-400:])


def _get_path(doc, path: str):
    cur = doc
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)] if part.isdigit() and int(part) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def grade_config(problem: dict, text: str, p) -> tuple[bool, str]:
    from .sandbox import run

    if problem["grader"] == "dockerfile":
        r = run({"Dockerfile": text}, "dockerfile", p=p)
        errors = [ln for ln in r.stdout.splitlines() if " error:" in ln.lower() or "DL3" in ln and "error" in ln.lower()]
        missing = [rx for rx in problem["require"] if not re.search(rx, text)]
        return (not errors and not missing), "; ".join(errors + [f"missing {m}" for m in missing])[:400]
    r = run({"manifest.yaml": text}, "kubernetes", p=p)
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return False, f"YAML error {e}"[:300]
    bad = [f"{k}={_get_path(doc, k)!r}" for k, v in problem["require"].items() if str(_get_path(doc, k)) != str(v)]
    ok = r.exit_code == 0 and not bad
    return ok, ("" if ok else (r.stdout.strip()[-200:] + " " + "; ".join(bad)))


# --- running ------------------------------------------------------------------------------------


async def _chat_collect(mm: ModelManager, model: str, msgs, think: bool, fmt=None, options=None) -> tuple[str, str, dict]:
    text, thinking, stats = [], [], {}
    try:
        async for c in mm.chat("tutor", msgs, model=model, think=think, fmt=fmt, options=options):
            text.append(c.text)
            thinking.append(c.thinking)
            if c.done:
                stats = c.stats
    except ProviderError as e:
        if think and "think" in str(e).lower():
            return await _chat_collect(mm, model, msgs, False, fmt, options)
        raise
    return "".join(text), "".join(thinking), stats


async def run_problem(mm: ModelManager, solver: SolverService, model: str, role: str, pr: dict, p, budget: int) -> dict:
    started = time.time()
    rec = {"id": pr["id"], "course": pr["course"], "kind": pr["kind"], "model": model, "role": role}
    think = role == "solver"
    try:
        if pr["grader"] in ("numeric", "symbolic", "set"):
            prompt = SOLVE_PROMPT.format(course_title=COURSE_TITLES.get(pr["course"], pr["course"]), text=pr["text"])
            if role == "solver":
                mm.roles["solver"] = model
                ref = await solver._one_run(prompt, {"thinking_budget": budget}, temperature=0.3)
                stats = mm.last_stats
                ver = verify(ref, pr["kind"], p) if ref else {"status": "none"}
                rec["verification"] = ver["status"]
            else:
                text, _, stats = await _chat_collect(mm, model, [{"role": "user", "content": prompt}], False, fmt=SCHEMA, options={"temperature": 0.2})
                ref = parse_json(text)
            rec["answer"] = (ref or {}).get("final_answer")
            rec["answer_sympy"] = (ref or {}).get("final_answer_sympy")
            rec["correct"] = grade_answer(pr, ref)
        else:
            tmpl = CODE_PROMPT if pr["grader"] == "code" else CONFIG_PROMPT
            prompt = tmpl.format(text=pr["text"], language=pr.get("language", ""))
            text, thinking, stats = await _chat_collect(mm, model, [{"role": "user", "content": prompt}], think, options={"temperature": 0.2, "num_predict": budget + 2000})
            code = extract_code(text)
            ok, detail = (grade_code(pr, code, p) if pr["grader"] == "code" else grade_config(pr, code, p))
            rec["correct"] = ok
            rec["detail"] = detail
            rec["thinking_chars"] = len(thinking)
        rec["stats"] = stats
    except (ProviderError, Preempted) as e:
        rec["correct"] = False
        rec["error"] = str(e)[:300]
    rec["elapsed_s"] = round(time.time() - started, 2)
    return rec


async def bench_run(models: list[str], roles: list[str], limit: int | None, budget: int, ids: list[str] | None = None, cooldown: float = 0) -> Path:
    p = paths()
    s = load_settings(p)
    runtime.ensure_ollama(s, p)
    mm = ModelManager(s)
    solver = SolverService(p, None, mm, lambda: s)
    problems = [x for x in load_problems() if not ids or x["id"] in ids][: limit or None]
    out = RESULTS / f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"
    RESULTS.mkdir(parents=True, exist_ok=True)
    records = []
    for model in models:
        for role in roles:
            for pr in problems:
                rec = await run_problem(mm, solver, model, role, pr, p, budget)
                records.append(rec)
                mark = "✓" if rec["correct"] else "✗"
                print(f"{mark} {model:<18} {role:<7} {pr['id']:<18} {rec['elapsed_s']:>6.1f}s {rec.get('answer') or rec.get('detail') or rec.get('error') or ''}"[:160], flush=True)
                out.write_text(json.dumps({"models": models, "roles": roles, "budget": budget, "records": records}, indent=1, default=str))
                if cooldown:
                    await asyncio.sleep(cooldown)  # let the machine cool between problems
        await mm.ollama.unload_all()
    write_report()
    return out


# --- latency, memory, concurrency ----------------------------------------------------------------

LATENCY_PROMPT = "Explain, in about 200 words, why the electric field inside a conductor in electrostatic equilibrium is zero. Use plain language."


async def bench_latency(models: list[str]) -> Path:
    p = paths()
    s = load_settings(p)
    runtime.ensure_ollama(s, p)
    mm = ModelManager(s)
    rows = []
    for model in models:
        await mm.ollama.unload_all()
        await asyncio.sleep(2)
        before = psutil.virtual_memory()
        msgs = [{"role": "user", "content": LATENCY_PROMPT}]
        _, _, cold = await _chat_collect(mm, model, msgs, False, options={"temperature": 0.2})
        warm_runs = []
        for _ in range(2):
            _, _, w = await _chat_collect(mm, model, msgs, False, options={"temperature": 0.2})
            warm_runs.append(w)
        loaded = await mm.ollama.loaded()
        after = psutil.virtual_memory()
        swap = psutil.swap_memory()
        size = next((m["size"] for m in loaded if m["name"] == model), 0)
        row = {
            "model": model,
            "resident_gb": round(size / GB, 2),
            "cold_total_s": cold.get("total_s"),
            "cold_load_s": cold.get("load_s"),
            "warm_ttft_s": round(statistics.mean(w["ttft_s"] for w in warm_runs), 2),
            "warm_tokens_per_s": round(statistics.mean(w["tokens_per_s"] for w in warm_runs), 1),
            "warm_total_s": round(statistics.mean(w["total_s"] for w in warm_runs), 1),
            "reply_tokens": round(statistics.mean(w["eval_tokens"] for w in warm_runs)),
            "available_before_gb": round(before.available / GB, 1),
            "available_after_gb": round(after.available / GB, 1),
            "swap_used_gb": round(swap.used / GB, 2),
        }
        rows.append(row)
        print(json.dumps(row), flush=True)
    await mm.ollama.unload_all()
    out = RESULTS / f"latency-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(rows, indent=1))
    write_report()
    return out


async def bench_concurrency(model: str | None = None) -> Path:
    """How long does a tutor reply take while the solver is mid-pass?
    (a) baseline, (b) both sent to Ollama at once (it queues them), (c) with our preemption."""
    p = paths()
    s = load_settings(p)
    runtime.ensure_ollama(s, p)
    mm = ModelManager(s)
    model = model or s["models"]["tutor"]
    mm.roles["solver"] = mm.roles["tutor"] = model
    tutor_msgs = [{"role": "user", "content": "In two sentences, what does Gauss's law say?"}]
    solve_msgs = [{"role": "user", "content": "Derive, step by step with full reasoning, the electric field everywhere for a uniformly charged solid sphere, then the potential everywhere."}]
    await _chat_collect(mm, model, tutor_msgs, False)  # warm up
    _, _, base = await _chat_collect(mm, model, tutor_msgs, False)

    async def solver_stream(background: bool):
        try:
            async for _ in mm.chat("solver", solve_msgs, model=model, think=True, background=background, options={"num_predict": 1500}):
                pass
            return "finished"
        except Preempted:
            return "preempted"

    async def raw_tutor():
        t0 = time.perf_counter()
        first = None
        async for c in mm.ollama.chat(model, tutor_msgs):
            if (c.text or c.thinking) and first is None:
                first = time.perf_counter() - t0
        return {"ttft_s": round(first or 0, 2), "total_s": round(time.perf_counter() - t0, 2)}

    # (b) both straight to Ollama: the tutor request waits behind the solver.
    solver_task = asyncio.create_task(_raw_stream(mm, model, solve_msgs))
    await asyncio.sleep(4)
    queued = await raw_tutor()
    await solver_task

    # (c) preemption: the solver stream is cancelled as soon as the tutor turn starts.
    solver_task = asyncio.create_task(solver_stream(True))
    await asyncio.sleep(4)
    t0 = time.perf_counter()
    first = None
    async for c in mm.chat("tutor", tutor_msgs, model=model):
        if c.text and first is None:
            first = time.perf_counter() - t0
    preempt = {"ttft_s": round(first or 0, 2), "total_s": round(time.perf_counter() - t0, 2), "solver": await solver_task}

    res = {"model": model, "baseline": {"ttft_s": base["ttft_s"], "total_s": base["total_s"]}, "queued_behind_solver": queued, "with_preemption": preempt,
           "num_parallel": s.get("ollama", {}).get("num_parallel", 1)}
    print(json.dumps(res, indent=1))
    out = RESULTS / f"concurrency-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=1))
    write_report()
    return out


async def _raw_stream(mm: ModelManager, model: str, msgs) -> None:
    async for _ in mm.ollama.chat(model, msgs, think=True, options={"num_predict": 1500}):
        pass


# --- report ---------------------------------------------------------------------------------------------


def write_report() -> Path:
    lines = ["# Benchmark results", "", "Generated by `tutor bench`. Machine: " + _machine(), ""]
    runs = sorted(RESULTS.glob("run-*.json"))
    if runs:
        recs = []
        for f in runs:
            recs += json.loads(f.read_text())["records"]
        # Latest record wins per (model, role, problem).
        latest = {}
        for r in recs:
            latest[(r["model"], r["role"], r["id"])] = r
        recs = list(latest.values())
        lines += ["## Accuracy", "", "| Model | Role | All | E&M | QM | SML | Cloud | Mean time/problem |", "|---|---|---|---|---|---|---|---|"]
        keys = sorted({(r["model"], r["role"]) for r in recs})
        for model, role in keys:
            rs = [r for r in recs if r["model"] == model and r["role"] == role]

            def acc(sel):
                xs = [r for r in rs if sel(r)]
                return f"{sum(r['correct'] for r in xs)}/{len(xs)}" if xs else "–"

            lines.append(
                f"| {model} | {role} | {acc(lambda r: True)} | {acc(lambda r: r['course'] == 'em')} | {acc(lambda r: r['course'] == 'qm')} | "
                f"{acc(lambda r: r['course'] == 'psl')} | {acc(lambda r: r['course'] == 'cloud')} | {statistics.mean(r['elapsed_s'] for r in rs):.1f}s |"
            )
        ver = [r for r in recs if r.get("verification")]
        if ver:
            lines += ["", "## Does verification predict correctness? (solver role)", "", "| Model | verified & correct | verified & wrong | unverified & correct | unverified & wrong |", "|---|---|---|---|---|"]
            for model in sorted({r["model"] for r in ver}):
                rs = [r for r in ver if r["model"] == model]
                vc = sum(1 for r in rs if r["verification"] == "verified" and r["correct"])
                vw = sum(1 for r in rs if r["verification"] == "verified" and not r["correct"])
                uc = sum(1 for r in rs if r["verification"] != "verified" and r["correct"])
                uw = sum(1 for r in rs if r["verification"] != "verified" and not r["correct"])
                lines.append(f"| {model} | {vc} | {vw} | {uc} | {uw} |")
    lat = sorted(RESULTS.glob("latency-*.json"))
    if lat:
        rows = {}
        for f in lat:
            for r in json.loads(f.read_text()):
                rows[r["model"]] = r
        lines += ["", "## Latency and memory (≈200-word reply, thinking off)", "",
                  "| Model | Resident GB | Cold total s | Warm TTFT s | Tokens/s | Warm total s | Free RAM after load GB | Swap GB |", "|---|---|---|---|---|---|---|---|"]
        for r in rows.values():
            lines.append(f"| {r['model']} | {r['resident_gb']} | {r['cold_total_s']} | {r['warm_ttft_s']} | {r['warm_tokens_per_s']} | {r['warm_total_s']} | {r['available_after_gb']} | {r['swap_used_gb']} |")
    conc = sorted(RESULTS.glob("concurrency-*.json"))
    if conc:
        c = json.loads(conc[-1].read_text())
        lines += ["", "## Tutor reply while the solver runs", "", f"Model: {c['model']}", "",
                  "| Case | TTFT s | Total s |", "|---|---|---|",
                  f"| Baseline (solver idle) | {c['baseline']['ttft_s']} | {c['baseline']['total_s']} |",
                  f"| Both sent to Ollama (queued) | {c['queued_behind_solver']['ttft_s']} | {c['queued_behind_solver']['total_s']} |",
                  f"| Preemption policy (solver yields) | {c['with_preemption']['ttft_s']} | {c['with_preemption']['total_s']} |"]
    out = RESULTS / "REPORT.md"
    out.write_text("\n".join(lines) + "\n")
    return out


def _machine() -> str:
    from .hardware import detect

    hw = detect()
    return f"{hw.chip}, {hw.ram_gb} GB"


def regrade() -> int:
    """Re-score stored answers with the current grader (after a grading fix)."""
    probs = {x["id"]: x for x in load_problems()}
    changed = 0
    for f in sorted(RESULTS.glob("run-*.json")):
        data = json.loads(f.read_text())
        for r in data["records"]:
            pr = probs.get(r["id"])
            if not pr or pr["grader"] not in ("numeric", "symbolic", "set") or "answer" not in r:
                continue
            ok = grade_answer(pr, {"final_answer": r.get("answer") or "", "final_answer_sympy": r.get("answer_sympy") or ""})
            if ok != r["correct"]:
                changed += 1
                r["correct"] = ok
        f.write_text(json.dumps(data, indent=1, default=str))
    write_report()
    return changed


def run(a) -> int:
    s = load_settings()
    models = a.models or [s["models"]["tutor"]]
    if a.action == "run":
        out = asyncio.run(bench_run(models, a.roles or ["tutor", "solver"], a.limit, s["solver"]["thinking_budget"], a.ids, a.cooldown))
    elif a.action == "latency":
        out = asyncio.run(bench_latency(models))
    elif a.action == "concurrency":
        out = asyncio.run(bench_concurrency(models[0]))
    elif a.action == "retrieval":
        from .retrieval_eval import run_eval

        out = asyncio.run(run_eval())
    elif a.action == "regrade":
        print(f"re-graded: {regrade()} records changed")
        out = RESULTS / "REPORT.md"
    else:
        out = write_report()
    print(f"\nwrote {out}")
    return 0
