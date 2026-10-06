import os

import pytest


@pytest.fixture(autouse=True)
def tutor_home(tmp_path, monkeypatch):
    """Every test gets its own config/data dirs; the real ones are never touched."""
    monkeypatch.setenv("MAGNUS_TUTOR_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAGNUS_TUTOR_TESTING", "1")
    monkeypatch.setenv("MAGNUS_TUTOR_MAGNUS_DIR", str(tmp_path / "magnus-config"))  # never the real ~/.config/magnus
    import magnus_tutor.db as dbm

    dbm._db = None
    dbm._db_path = None
    yield tmp_path / "home"
    if dbm._db is not None:
        dbm._db.close()
        dbm._db = None


@pytest.fixture
def p(tutor_home):
    from magnus_tutor.config import paths
    from magnus_tutor.setup import bootstrap

    pp = paths()
    bootstrap(pp)
    return pp


class FakeProvider:
    """Scripted stand-in for Ollama: replies via a function of the messages."""

    name = "fake"
    local = True

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or (lambda messages, **kw: "OK")

    async def chat(self, model, messages, *, think=False, options=None, fmt=None):
        from magnus_tutor.llm.base import ChatChunk

        self.calls.append({"model": model, "messages": messages, "think": think, "fmt": fmt})
        text = self.reply(messages, think=think, fmt=fmt, model=model)
        for i in range(0, len(text), 12):
            yield ChatChunk(text=text[i : i + 12])
        yield ChatChunk(done=True, stats={"total_s": 0.01, "ttft_s": 0.0, "eval_tokens": len(text) // 4, "tokens_per_s": 100})

    async def embed(self, model, texts):
        import hashlib

        out = []
        for t in texts:
            h = hashlib.sha256(t.lower().encode()).digest()
            vec = [0.0] * 64
            for w in t.lower().split():
                vec[int(hashlib.md5(w.strip(".,?!").encode()).hexdigest(), 16) % 64] += 1.0
            out.append(vec or [h[0] / 255.0] * 64)
        return out

    async def available(self):
        return True

    async def loaded(self):
        return []

    async def list_models(self):
        return [{"name": "fake", "size": 1, "details": {}}]

    async def unload(self, model):
        pass

    async def unload_all(self):
        return []


@pytest.fixture
def fake_models(p):
    from magnus_tutor.config import load_settings
    from magnus_tutor.llm.manager import ModelManager

    mm = ModelManager(load_settings(p))
    fp = FakeProvider()
    mm.ollama = fp

    async def _prepare(model):
        return None

    mm._prepare = _prepare
    return mm, fp
