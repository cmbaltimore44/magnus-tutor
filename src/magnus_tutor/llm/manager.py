"""Model roles, one-large-model-at-a-time, and tutor-first scheduling.

- Roles (tutor, solver, coder, vision, embedding) map to models in settings.
- Before a large model is used, any *other* large model is unloaded, so only
  one is ever resident (the small embedding model may stay alongside).
- Foreground (tutor) requests always win: a running background stream (the
  hidden solver) is cancelled the moment a tutor turn starts, and background
  work waits until no tutor turn is in flight. The solver resumes afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator

from ..hardware import memory_preflight
from .base import ChatChunk, Message, ProviderError
from .ollama import OllamaProvider


class Preempted(Exception):
    """A background stream was cancelled because a tutor turn started."""


class ModelManager:
    def __init__(self, settings: dict):
        self.reload(settings)
        self._foreground = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._preempt = asyncio.Event()
        self._swap_lock = asyncio.Lock()
        self._bg_lock = asyncio.Lock()  # one background stream at a time (solver, vision ingestion)
        self.cloud = None  # set by enable_cloud()
        self.last_stats: dict = {}
        self.warnings: list[str] = []

    def reload(self, settings: dict) -> None:
        self.settings = settings
        o = settings["ollama"]
        self.ollama = OllamaProvider(o["host"], o["keep_alive"], o["num_ctx"], o.get("embed_num_ctx", 2048))
        self.roles = dict(settings["models"])

    def model_for(self, role: str) -> str:
        return self.roles.get(role) or self.roles["tutor"]

    @property
    def embedding_model(self) -> str:
        return self.roles["embedding"]

    # --- scheduling ---------------------------------------------------------

    @property
    def foreground_active(self) -> bool:
        return self._foreground > 0

    @contextlib.asynccontextmanager
    async def foreground(self):
        self._foreground += 1
        self._idle.clear()
        self._preempt.set()
        try:
            yield
        finally:
            self._foreground -= 1
            if self._foreground == 0:
                self._preempt.clear()
                self._idle.set()

    async def wait_idle(self) -> None:
        await self._idle.wait()

    # --- one large model at a time -------------------------------------------------

    async def _prepare(self, model: str) -> None:
        async with self._swap_lock:
            loaded = await self.ollama.loaded()
            others = [m for m in loaded if m["name"] != model and m["name"] != self.embedding_model]
            for m in others:
                await self.ollama.unload(m["name"])
            if not any(m["name"] == model for m in loaded):
                info = await self.ollama.list_models()
                size = next((m["size"] for m in info if m["name"] == model), 0) / 1024**3
                if size == 0:
                    raise ProviderError(f"model {model} isn't installed. Pull it from Settings → Models (or `tutor models pull {model}`).")
                pf = memory_preflight(size * 1.2)
                if not pf["ok"]:
                    self.warnings.append(
                        f"Low memory: {pf['available_gb']} GB free, {model} needs about {pf['needed_gb']} GB. Close some apps or switch to the Light preset."
                    )

    # --- chat ------------------------------------------------------------------------

    async def chat(
        self,
        role: str,
        messages: list[Message],
        *,
        think: bool = False,
        options: dict | None = None,
        fmt: dict | str | None = None,
        background: bool = False,
        model: str | None = None,
        provider: str = "ollama",
    ) -> AsyncIterator[ChatChunk]:
        if provider == "anthropic":
            if not self.cloud:
                raise ProviderError("The cloud provider is off. Turn it on in Settings first.")
            async for c in self.cloud.chat(model or self.cloud.model, messages, think=think, options=options, fmt=fmt):
                yield c
            return
        model = model or self.model_for(role)
        if background:
            async with self._bg_lock:
                await self.wait_idle()
                await self._prepare(model)
                async for c in self._preemptible(self.ollama.chat(model, messages, think=think, options=options, fmt=fmt)):
                    if c.done:
                        self.last_stats = {**c.stats, "model": model, "role": role}
                    yield c
            return
        async with self.foreground():
            await self._prepare(model)
            async for c in self.ollama.chat(model, messages, think=think, options=options, fmt=fmt):
                if c.done:
                    self.last_stats = {**c.stats, "model": model, "role": role}
                yield c

    async def _preemptible(self, stream: AsyncIterator[ChatChunk]) -> AsyncIterator[ChatChunk]:
        it = stream.__aiter__()
        while True:
            if self._preempt.is_set():
                await _aclose(it)
                raise Preempted()
            nxt = asyncio.ensure_future(it.__anext__())
            stop = asyncio.ensure_future(self._preempt.wait())
            done, _ = await asyncio.wait({nxt, stop}, return_when=asyncio.FIRST_COMPLETED)
            if nxt in done:
                stop.cancel()
                try:
                    chunk = nxt.result()
                except StopAsyncIteration:
                    return
                yield chunk
            else:
                nxt.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                    await nxt
                await _aclose(it)
                raise Preempted()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self.ollama.embed(self.embedding_model, texts)


async def _aclose(it) -> None:
    close = getattr(it, "aclose", None)
    if close:
        with contextlib.suppress(Exception):
            await close()
