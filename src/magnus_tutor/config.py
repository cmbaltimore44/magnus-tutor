"""Paths and settings.

Hand-edited files live in ~/.config/magnus-tutor/ (settings, profile, courses,
prompts, languages). Generated data (SQLite, page cache, run state) lives in
~/.local/share/magnus-tutor/. MAGNUS_TUTOR_HOME points both somewhere else
(tests use a temp dir).
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path

    @property
    def settings(self) -> Path:
        return self.config / "settings.yaml"

    @property
    def profile(self) -> Path:
        return self.config / "profile.yaml"

    @property
    def courses(self) -> Path:
        return self.config / "courses"

    @property
    def prompts(self) -> Path:
        return self.config / "prompts"

    @property
    def languages(self) -> Path:
        return self.config / "languages.yaml"

    @property
    def db(self) -> Path:
        return self.data / "tutor.db"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    @property
    def run(self) -> Path:
        return self.data / "run"

    @property
    def uploads(self) -> Path:
        return self.data / "uploads"

    @property
    def materials_root(self) -> Path:
        """Where course folders (notes, textbooks, other) are created by default."""
        override = os.environ.get("MAGNUS_TUTOR_MATERIALS")
        if override:
            return Path(override).expanduser()
        if os.environ.get("MAGNUS_TUTOR_HOME"):
            return self.data / "materials"
        return Path.home() / "Documents" / "Magnus"


def paths() -> Paths:
    home = os.environ.get("MAGNUS_TUTOR_HOME")
    if home:
        base = Path(home).expanduser()
        return Paths(config=base / "config", data=base / "data")
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return Paths(config=xdg_config / "magnus-tutor", data=xdg_data / "magnus-tutor")


DEFAULT_SETTINGS: dict[str, Any] = {
    "preset": "standard",  # light | standard | full (set by the hardware check on first run)
    "models": {
        "tutor": "qwen3.5:9b",
        "solver": "qwen3.5:9b",
        "coder": "qwen3.5:9b",
        "vision": "qwen3.5:9b",
        "embedding": "qwen3-embedding:0.6b",
    },
    "ollama": {
        "host": "http://127.0.0.1:11434",
        "manage_server": True,  # start `ollama serve` with our limits if it isn't running
        "keep_alive": "3m",
        "num_ctx": 8192,
        "embed_num_ctx": 2048,
    },
    "server": {"port": 8765, "idle_shutdown_minutes": 30},
    "gates": {
        "attempt_gate": True,
        "hint_ladder": True,
        "solution_gate": True,
        "output_check": True,
    },
    "solver": {
        "enabled": True,
        "thinking_budget": 5000,
        "self_consistency": 1,
        # auto: measured policy; idle_only: run only between turns and yield to the tutor
        "policy": "idle_only",
    },
    "background": {"only_when_plugged_in": True, "paused": False},
    "cache": {"max_mb": 2048},
    "alerts": "auto",  # auto | terminal | web | both
    "appearance": {"theme": "heather", "mode": "auto"},
    "cloud": {"enabled": False, "model": "claude-sonnet-5-5", "max_tokens": 4096},
    "magnus": {"command": "magnus"},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(path: Path, default: Any = None) -> Any:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
        return default if data is None else data
    except FileNotFoundError:
        return default


def write_yaml_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    os.replace(tmp, path)


def load_settings(p: Paths | None = None) -> dict[str, Any]:
    p = p or paths()
    return deep_merge(DEFAULT_SETTINGS, load_yaml(p.settings, {}) or {})


def save_settings(changes: dict[str, Any], p: Paths | None = None) -> dict[str, Any]:
    """Merge `changes` into settings.yaml (only the user's overrides are stored)."""
    p = p or paths()
    current = load_yaml(p.settings, {}) or {}
    merged = deep_merge(current, changes)
    write_yaml_atomic(p.settings, merged)
    return load_settings(p)
