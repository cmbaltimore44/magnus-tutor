"""`tutor models`: list, pull (always shows the size and asks), rm, unload."""

from __future__ import annotations

import asyncio
import sys

import httpx

from . import runtime
from .config import load_settings
from .llm.ollama import OllamaProvider


def download_size(model: str) -> int | None:
    """Total layer size from the Ollama registry manifest, before pulling."""
    name, _, tag = model.partition(":")
    tag = tag or "latest"
    repo = name if "/" in name else f"library/{name}"
    try:
        r = httpx.get(
            f"https://registry.ollama.ai/v2/{repo}/manifests/{tag}",
            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        m = r.json()
        return sum(layer.get("size", 0) for layer in m.get("layers", [])) + m.get("config", {}).get("size", 0)
    except (httpx.HTTPError, ValueError):
        return None


def run(a) -> int:
    s = load_settings()
    if not runtime.ensure_ollama(s)["running"]:
        print("tutor: Ollama isn't running", file=sys.stderr)
        return 1
    o = OllamaProvider(s["ollama"]["host"])
    if a.action == "list":
        roles = {}
        for role, m in s["models"].items():
            roles.setdefault(m, []).append(role)
        for m in asyncio.run(o.list_models()):
            print(f"{m['name']:<28} {m['size'] / 1024**3:6.1f} GB  {', '.join(roles.get(m['name'], [])) or '(no role)'}")
        return 0
    if a.action == "loaded":
        for m in asyncio.run(o.loaded()):
            print(f"{m['name']:<28} {m['size'] / 1024**3:6.1f} GB  expires {m['expires_at']}")
        return 0
    if a.action == "unload":
        print("unloaded: " + (", ".join(asyncio.run(o.unload_all())) or "nothing was loaded"))
        return 0
    if not a.name:
        print(f"tutor models {a.action}: name a model", file=sys.stderr)
        return 1
    if a.action == "rm":
        print(("removed " if asyncio.run(o.delete(a.name)) else "not installed: ") + a.name)
        return 0
    if a.action == "pull":
        size = download_size(a.name)
        size_txt = f"{size / 1024**3:.1f} GB" if size else "unknown size"
        if not a.yes:
            ans = input(f"Pull {a.name} ({size_txt})? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("cancelled")
                return 1

        async def pull():
            last = ""
            async for ev in o.pull(a.name):
                st = ev.get("status", "")
                if ev.get("total") and ev.get("completed"):
                    pct = 100 * ev["completed"] / ev["total"]
                    print(f"\r{st[:40]:<40} {pct:5.1f}%", end="", flush=True)
                elif st != last:
                    print(f"\n{st}", end="", flush=True)
                last = st
            print()

        asyncio.run(pull())
        return 0
    return 1
