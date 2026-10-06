"""Ollama provider (local, default) plus model management: list, pull, unload, ps."""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx

from .base import ChatChunk, Message, ProviderError


class OllamaProvider:
    name = "ollama"
    local = True

    def __init__(self, host: str = "http://127.0.0.1:11434", keep_alive: str = "3m", num_ctx: int = 8192, embed_num_ctx: int = 2048):
        self.host = host.rstrip("/")
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.embed_num_ctx = embed_num_ctx

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.host, timeout=httpx.Timeout(timeout, connect=5.0))

    async def available(self) -> bool:
        try:
            async with self._client(3) as c:
                r = await c.get("/api/version")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        think: bool = False,
        options: dict | None = None,
        fmt: dict | str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": think,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, **(options or {})},
        }
        if fmt is not None:
            body["format"] = fmt
        started = time.perf_counter()
        first_token = None
        try:
            async with self._client(None) as c:
                async with c.stream("POST", "/api/chat", json=body) as r:
                    if r.status_code != 200:
                        detail = (await r.aread()).decode(errors="replace")
                        raise ProviderError(f"ollama {r.status_code}: {detail[:300]}")
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        d = json.loads(line)
                        if "error" in d:
                            raise ProviderError(f"ollama: {d['error']}")
                        msg = d.get("message") or {}
                        text, thinking = msg.get("content", ""), msg.get("thinking", "")
                        if (text or thinking) and first_token is None:
                            first_token = time.perf_counter() - started
                        if d.get("done"):
                            yield ChatChunk(text=text, thinking=thinking, done=True, stats=_stats(d, started, first_token))
                            return
                        yield ChatChunk(text=text, thinking=thinking)
        except httpx.ConnectError as e:
            raise ProviderError("Ollama isn't running (start the tutor with `tutor start`)") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"lost the connection to Ollama ({type(e).__name__})") from e

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with self._client(120) as c:
                r = await c.post(
                    "/api/embed",
                    json={"model": model, "input": texts, "keep_alive": self.keep_alive, "options": {"num_ctx": self.embed_num_ctx}, "truncate": True},
                )
        except httpx.HTTPError as e:
            raise ProviderError(f"Ollama embeddings unavailable ({type(e).__name__})") from e
        if r.status_code != 200:
            raise ProviderError(f"ollama embed {r.status_code}: {r.text[:300]}")
        return r.json()["embeddings"]

    # --- model management -------------------------------------------------

    async def list_models(self) -> list[dict]:
        async with self._client(10) as c:
            r = await c.get("/api/tags")
            r.raise_for_status()
            return [{"name": m["name"], "size": m.get("size", 0), "details": m.get("details", {})} for m in r.json().get("models", [])]

    async def loaded(self) -> list[dict]:
        try:
            async with self._client(5) as c:
                r = await c.get("/api/ps")
                r.raise_for_status()
                return [
                    {"name": m["name"], "size": m.get("size", 0), "vram": m.get("size_vram", 0), "expires_at": m.get("expires_at"), "context": m.get("context_length")}
                    for m in r.json().get("models", [])
                ]
        except httpx.HTTPError:
            return []

    async def unload(self, model: str) -> None:
        async with self._client(30) as c:
            # keep_alive 0 with no prompt unloads the model right away.
            r = await c.post("/api/generate", json={"model": model, "keep_alive": 0})
            if r.status_code not in (200, 404):
                raise ProviderError(f"unload {model}: {r.text[:200]}")

    async def unload_all(self) -> list[str]:
        names = [m["name"] for m in await self.loaded()]
        for n in names:
            await self.unload(n)
        return names

    async def show(self, model: str) -> dict | None:
        async with self._client(10) as c:
            r = await c.post("/api/show", json={"model": model})
            return r.json() if r.status_code == 200 else None

    async def pull(self, model: str) -> AsyncIterator[dict]:
        async with self._client(None) as c:
            async with c.stream("POST", "/api/pull", json={"model": model, "stream": True}) as r:
                async for line in r.aiter_lines():
                    if line:
                        yield json.loads(line)

    async def delete(self, model: str) -> bool:
        async with self._client(30) as c:
            r = await c.request("DELETE", "/api/delete", json={"model": model})
            return r.status_code == 200


def _stats(d: dict, started: float, first_token: float | None) -> dict:
    ns = 1e9
    eval_count = d.get("eval_count", 0)
    eval_s = d.get("eval_duration", 0) / ns
    return {
        "total_s": round(time.perf_counter() - started, 3),
        "ttft_s": round(first_token, 3) if first_token is not None else None,
        "load_s": round(d.get("load_duration", 0) / ns, 3),
        "prompt_tokens": d.get("prompt_eval_count", 0),
        "eval_tokens": eval_count,
        "tokens_per_s": round(eval_count / eval_s, 1) if eval_s else None,
    }
