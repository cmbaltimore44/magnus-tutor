"""Optional cloud provider (Anthropic). Off by default; used only when you save an
API key (kept in the macOS Keychain, like Magnus's session) and turn it on, and
then only for replies you explicitly escalate. Those replies are marked in the UI
because the conversation leaves this Mac.
"""

from __future__ import annotations

import base64
import re
import subprocess
from typing import AsyncIterator

from .base import ChatChunk, ProviderError

KEYCHAIN_SERVICE = "magnus-tutor-anthropic"
ACCOUNT = "api-key"


def get_api_key() -> str | None:
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", ACCOUNT, "-w"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    key = r.stdout.strip()
    return key if r.returncode == 0 and key else None


def set_api_key(key: str) -> None:
    # Sent on stdin to `security -i` so the key never appears in a process's argv (visible to ps).
    if not re.fullmatch(r"sk-ant-[A-Za-z0-9_-]{10,200}", key):
        raise ProviderError("That doesn't look like an Anthropic API key.")
    cmd = f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {ACCOUNT} -w {key}\n"
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=10)
    if r.returncode != 0 or get_api_key() != key:
        raise ProviderError("couldn't save the key to the Keychain")


def delete_api_key() -> None:
    subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", ACCOUNT], capture_output=True, timeout=5)


def _media_type(b64: str) -> str:
    head = base64.b64decode(b64[:24] + "==")[:8] if b64 else b""
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"RIFF":
        return "image/webp"
    if head[:3] == b"GIF":
        return "image/gif"
    return "image/png"


def to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """Our {role, content, images} list → (system, Anthropic messages). Every system
    message is folded into the top-level system prompt."""
    system, out = [], []
    for m in messages:
        if m["role"] == "system":
            system.append(m["content"])
            continue
        content: list[dict] = [{"type": "image", "source": {"type": "base64", "media_type": _media_type(b), "data": b}} for b in m.get("images") or []]
        content.append({"type": "text", "text": m["content"] or "(image)"})
        out.append({"role": m["role"], "content": content})
    return "\n\n".join(system), out


class AnthropicProvider:
    name = "anthropic"
    local = False

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", max_tokens: int = 16000):
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    async def available(self) -> bool:
        return True

    async def chat(self, model: str, messages: list[dict], *, think: bool = False, options: dict | None = None, fmt=None) -> AsyncIterator[ChatChunk]:
        import anthropic

        system, msgs = to_anthropic(messages)
        try:
            # On a policy decline the API re-runs the request on Anthropic's recommended
            # fallback model inside the same call ("default" routes by refusal category).
            async with self.client.beta.messages.stream(
                model=model or self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=msgs,
                output_config={"effort": "high" if think else "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for text in stream.text_stream:
                    yield ChatChunk(text=text)
                final = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise ProviderError("The Anthropic API key was rejected. Update it in Settings → Optional cloud model.") from e
        except anthropic.RateLimitError as e:
            raise ProviderError("Anthropic rate limit reached; try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("Couldn't reach the Anthropic API (offline?).") from e
        if final.stop_reason == "refusal":
            yield ChatChunk(text="\n\n_(The cloud model declined this request.)_")
        u = final.usage
        yield ChatChunk(done=True, stats={"model": final.model, "prompt_tokens": u.input_tokens, "eval_tokens": u.output_tokens, "provider": "anthropic",
                                          "stop_reason": final.stop_reason})

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise ProviderError("embeddings always stay local")


def configure_cloud(st) -> None:
    """Attach (or detach) the cloud provider according to settings and the stored key."""
    c = st.settings.get("cloud", {})
    key = get_api_key() if c.get("enabled") else None
    st.models.cloud = AnthropicProvider(key, c.get("model", "claude-opus-5-5"), int(c.get("max_tokens", 16000))) if key else None
