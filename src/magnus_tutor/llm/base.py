"""The LLMProvider interface. Ollama is the default; Anthropic is optional."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


class ProviderError(RuntimeError):
    pass


@dataclass
class ChatChunk:
    text: str = ""
    thinking: str = ""
    done: bool = False
    stats: dict = field(default_factory=dict)


# A message is a plain dict: {"role": "system"|"user"|"assistant", "content": str,
# "images": [base64 str, ...] (optional)}.
Message = dict


class LLMProvider(Protocol):
    name: str
    local: bool  # False means data leaves the machine

    def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        think: bool = False,
        options: dict | None = None,
        fmt: dict | str | None = None,
    ) -> AsyncIterator[ChatChunk]: ...

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]: ...

    async def available(self) -> bool: ...


async def collect(stream: AsyncIterator[ChatChunk]) -> ChatChunk:
    """Drain a chat stream into one chunk (text, thinking, final stats)."""
    text, thinking, stats = [], [], {}
    async for c in stream:
        text.append(c.text)
        thinking.append(c.thinking)
        if c.done:
            stats = c.stats
    return ChatChunk(text="".join(text), thinking="".join(thinking), done=True, stats=stats)
