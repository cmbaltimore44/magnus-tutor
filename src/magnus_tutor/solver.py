"""The hidden solver pass: solve privately, verify with tools, assign confidence.

Runs in the background while the student works on their own attempt. Tutor
turns always win: a solver stream is cancelled the moment a tutor turn starts
and restarts once the tutor is idle again (ModelManager handles preemption).

Confidence:
  verified   verification tools confirmed the answer (SymPy/numeric/units/tests), nothing failed
  agreed     several independent runs agree, no check failed
  uncertain  anything else (the tutor must say it isn't sure)
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from . import prompts, store
from .config import Paths
from .context import base_variables, format_sources
from .courses import load_course
from .db import DB
from .llm.base import ProviderError
from .llm.manager import ModelManager, Preempted
from .tools import mathcheck as M
from .tools.verify import verify

SCHEMA = {
    "type": "object",
    "properties": {
        "final_answer": {"type": "string"},
        "final_answer_sympy": {"type": "string"},
        "units": {"type": "string"},
        "symbol_units": {"type": "object"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "key_concepts": {"type": "array", "items": {"type": "string"}},
        "common_mistakes": {"type": "array", "items": {"type": "string"}},
        "checks": {"type": "array", "items": {"type": "object"}},
        "language": {"type": "string"},
        "reference_code": {"type": "string"},
        "tests": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["final_answer", "steps", "key_concepts"],
}


def parse_json(text: str) -> dict | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        d = json.loads(text, strict=False)
    except json.JSONDecodeError:
        try:
            d = json.loads(re.sub(r"\\(?![\"\\/bfnrtu])", r"\\\\", text), strict=False)  # LaTeX backslashes
        except json.JSONDecodeError:
            return None
    if not (isinstance(d, dict) and d.get("final_answer") is not None):
        return None
    return {k: (v if k in ("reference_code", "tests") else _fix_latex(v)) for k, v in d.items()}


def _fix_latex(v):
    """Models write LaTeX in JSON with single backslashes, so \frac arrives as a form
    feed + 'rac', \theta as a tab + 'heta'. Put the backslashes back."""
    if isinstance(v, str):
        v = v.replace("\x0c", "\\f").replace("\x08", "\\b").replace("\t", "\\t")
        v = re.sub(r"\r(?=[a-z])", r"\\r", v)
        v = re.sub(r"\n(?=(abla|eq|u\b|u_|u\^|ot\b|ewline))", r"\\n", v)
        return v
    if isinstance(v, list):
        return [_fix_latex(x) for x in v]
    if isinstance(v, dict):
        return {k: _fix_latex(x) for k, x in v.items()}
    return v


class SolverService:
    def __init__(self, p: Paths, db: DB, models: ModelManager, settings, *, retriever=None, publish=None):
        self.p, self.db, self.models, self.settings = p, db, models, settings
        self.retriever = retriever
        self.publish = publish or (lambda kind, data: None)
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.current: int | None = None

    # --- queue -----------------------------------------------------------------------------

    def start(self, pid: int) -> None:
        self.db.execute("UPDATE problems SET solver_status = 'pending' WHERE id = ?", (pid,))
        self.queue.put_nowait(pid)
        self.publish("solver", {"problem_id": pid, "status": "pending"})
        if self.worker is None or self.worker.done():
            self.worker = asyncio.get_event_loop().create_task(self._work())

    async def _work(self) -> None:
        while not self.queue.empty():
            pid = await self.queue.get()
            self.current = pid
            try:
                await self.solve(pid)
            except Exception as e:  # never let one problem kill the worker
                self.db.execute("UPDATE problems SET solver_status = 'failed', solver_meta = ? WHERE id = ?", (json.dumps({"error": str(e)[:300]}), pid))
                self.publish("solver", {"problem_id": pid, "status": "failed", "error": str(e)[:200]})
            finally:
                self.current = None

    # --- one problem -------------------------------------------------------------------------

    async def solve(self, pid: int) -> dict:
        pr = store.get_problem(self.db, pid)
        if not pr:
            return {}
        cfg = self.settings()["solver"]
        course = load_course(pr["course"], self.p) if pr["course"] else None
        started = time.time()
        self.db.execute("UPDATE problems SET solver_status = 'running' WHERE id = ?", (pid,))
        self.publish("solver", {"problem_id": pid, "status": "running"})

        passages = []
        if self.retriever:
            try:
                passages = await self.retriever.search(pr["text"], course=pr["course"], k=3)
            except Exception:
                passages = []
        v = base_variables(course, self.p)
        v.update({"problem": pr["text"], "retrieved_context": format_sources(passages)})
        prompt = prompts.get("solver", v, course, self.p)

        n_runs = max(1, int(cfg.get("self_consistency", 1)))
        runs, verifications, preemptions = [], [], 0
        for i in range(n_runs + (1 if n_runs == 1 and cfg.get("extra_run_if_unverified", True) else 0)):
            if i >= n_runs and verifications and verifications[0]["status"] == "verified":
                break  # the extra run is only for answers the tools couldn't verify
            while True:
                try:
                    ref = await self._one_run(prompt, cfg, temperature=0.3 if i == 0 else 0.7)
                    break
                except Preempted:
                    preemptions += 1
                    self.publish("solver", {"problem_id": pid, "status": "paused"})
                    await self.models.wait_idle()
                    await asyncio.sleep(0.5)
                    self.publish("solver", {"problem_id": pid, "status": "running"})
            if ref is None:
                continue
            ver = await asyncio.to_thread(verify, ref, pr["kind"] or "math", self.p)
            runs.append(ref)
            verifications.append(ver)

        if not runs:
            meta = {"error": "solver produced no usable answer", "elapsed_s": round(time.time() - started, 1), "preemptions": preemptions}
            self.db.execute("UPDATE problems SET solver_status = 'failed', confidence = 'uncertain', solver_meta = ? WHERE id = ?", (json.dumps(meta), pid))
            self.publish("solver", {"problem_id": pid, "status": "failed"})
            return meta

        best, confidence, agreement = choose(runs, verifications)
        best_ver = verifications[runs.index(best)]
        meta = {
            "elapsed_s": round(time.time() - started, 1),
            "model": self.models.model_for("solver"),
            "runs": len(runs),
            "agreement": agreement,
            "preemptions": preemptions,
            "verification": best_ver,
            "verification_summary": best_ver["summary"],
            "stats": self.models.last_stats,
        }
        self.db.update("problems", pid, reference_solution=best, confidence=confidence, solver_status="done", solver_meta=meta)
        self.publish("solver", {"problem_id": pid, "status": "done", "confidence": confidence})
        return {"reference": best, "confidence": confidence, **meta}

    async def _one_run(self, prompt: str, cfg: dict, temperature: float) -> dict | None:
        budget = int(cfg.get("thinking_budget", 2500))
        msgs = [{"role": "user", "content": prompt}]
        text, thinking, over = [], [], False
        async for c in self.models.chat("solver", msgs, think=True, background=True,
                                        options={"temperature": temperature, "num_predict": budget + 2500}):
            text.append(c.text)
            thinking.append(c.thinking)
            if len("".join(thinking)) / 3.5 > budget and not "".join(text).strip():
                over = True
                break
        ref = None if over else parse_json("".join(text))
        if ref is None:
            # Out of thinking budget (or unparsable): ask for the JSON directly, with the reasoning so far.
            reasoning = "".join(thinking)[-8000:]
            follow = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": f"(My reasoning so far)\n{reasoning}\n{''.join(text)}"},
                {"role": "user", "content": "Stop reasoning now. Output only the JSON object with the keys requested, using your best answer."},
            ]
            out = []
            async for c in self.models.chat("solver", follow, think=False, background=True, fmt=SCHEMA,
                                            options={"temperature": 0.1, "num_predict": 1800}):
                out.append(c.text)
            ref = parse_json("".join(out))
        return _clean(ref) if ref else None


def _clean(ref: dict) -> dict:
    for k in ("steps", "key_concepts", "common_mistakes", "checks", "tests"):
        if not isinstance(ref.get(k), list):
            ref[k] = []
    for k in ("final_answer", "final_answer_sympy", "units", "language", "reference_code"):
        if ref.get(k) is not None and not isinstance(ref[k], str):
            ref[k] = str(ref[k])
    if not isinstance(ref.get("symbol_units"), dict):
        ref["symbol_units"] = {}
    return ref


def same_answer(a: dict, b: dict) -> bool:
    ea, eb = a.get("final_answer_sympy") or a.get("final_answer"), b.get("final_answer_sympy") or b.get("final_answer")
    r = M.equivalent(ea, eb, rel_tol=5e-3)
    if r is None:
        na, nb = M.numeric_value(a.get("final_answer", "")), M.numeric_value(b.get("final_answer", ""))
        if na is not None and nb is not None:
            return M.sig_close(na, nb, 5e-3)
        return str(a.get("final_answer", "")).strip().lower() == str(b.get("final_answer", "")).strip().lower()
    return r


def choose(runs: list[dict], vers: list[dict]) -> tuple[dict, str, str]:
    """Pick the best run and decide confidence."""
    order = sorted(range(len(runs)), key=lambda i: {"verified": 0, "unverified": 1, "failed": 2}[vers[i]["status"]])
    best_i = order[0]
    best = runs[best_i]
    agree = sum(1 for r in runs if same_answer(r, best))
    agreement = f"{agree}/{len(runs)}"
    disagree = agree < len(runs)
    if vers[best_i]["status"] == "verified" and not disagree:
        conf = "verified"
    elif vers[best_i]["status"] == "verified" and disagree:
        conf = "agreed" if agree >= 2 else "uncertain"
    elif agree >= 2 and vers[best_i]["status"] != "failed":
        conf = "agreed"
    else:
        conf = "uncertain"
    return best, conf, agreement


async def solve_now(service: SolverService, pid: int) -> dict:
    """Synchronous solve (benchmarks, on-demand 'Prepare reference')."""
    try:
        return await service.solve(pid)
    except ProviderError as e:
        return {"error": str(e)}
