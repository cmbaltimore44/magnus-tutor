"""`tutor bench retrieval`: hit rate of the retriever on bench/retrieval.yaml."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

from .config import load_settings
from .db import get_db
from .llm.manager import ModelManager
from .retrieval import Retriever

ROOT = Path(__file__).resolve().parents[2] / "bench"


def _hit(passage: dict, expect: list[str]) -> bool:
    for e in expect:
        e = str(e)
        if e.startswith("p."):
            if str(passage.get("printed_page")) == e[2:].strip():
                return True
        elif re.search(rf"(?:^|> ){re.escape(e)}(?:\s|$)", passage.get("section_path") or ""):
            return True
    return False


async def run_eval() -> Path:
    spec = yaml.safe_load((ROOT / "retrieval.yaml").read_text())
    s = load_settings()
    r = Retriever(get_db(), ModelManager(s))
    k = spec.get("k", 5)
    rows, hits1, hitsk, ms = [], 0, 0, []
    await r.search("warm up", course=spec.get("course"))
    for item in spec["questions"]:
        t = time.perf_counter()
        res = await r.search(item["q"], course=spec.get("course"), k=k)
        ms.append((time.perf_counter() - t) * 1000)
        ranks = [i for i, p in enumerate(res) if _hit(p, item["expect"])]
        rank = ranks[0] + 1 if ranks else None
        hits1 += rank == 1
        hitsk += rank is not None
        rows.append({"q": item["q"], "expect": item["expect"], "rank": rank, "top": [p["label"] for p in res[:3]]})
        print(f"{'✓' if rank else '✗'} {rank or '-':>2}  {item['q']}", flush=True)
    n = len(spec["questions"])
    summary = {"questions": n, "hit@1": round(hits1 / n, 3), f"hit@{k}": round(hitsk / n, 3), "median_ms": round(sorted(ms)[n // 2], 1), "max_ms": round(max(ms), 1)}
    print(json.dumps(summary))
    out = ROOT / "results" / f"retrieval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    return out
