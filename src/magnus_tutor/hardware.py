"""Hardware check: chip, RAM, memory pressure, and model recommendations.

Rule of thumb for 4-bit models on unified memory: ~0.6 GB per billion
parameters plus KV cache and runtime overhead. We never recommend models whose
weights use more than about half of total RAM, so macOS, the browser,
GoodNotes and Magnus keep their room.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import asdict, dataclass

import psutil

GB = 1024**3


@dataclass
class Hardware:
    chip: str
    ram_gb: float
    apple_silicon: bool
    available_gb: float
    on_battery: bool | None


# Candidate catalogue: tag, download size (GB), role tags, notes. Sizes are from
# the Ollama library (Oct 2026). The benchmark (bench/) picks the defaults.
CATALOGUE = [
    {"tag": "qwen3.5:4b", "gb": 3.4, "roles": ["tutor", "solver", "coder", "vision"], "tier": "light"},
    {"tag": "qwen3.5:9b", "gb": 6.6, "roles": ["tutor", "solver", "coder", "vision"], "tier": "standard"},
    {"tag": "gemma4:12b", "gb": 8.0, "roles": ["tutor", "solver", "vision"], "tier": "standard"},
    {"tag": "qwen3:14b", "gb": 9.3, "roles": ["tutor", "solver", "coder"], "tier": "standard"},
    {"tag": "deepseek-r1:14b", "gb": 9.0, "roles": ["solver"], "tier": "standard"},
    {"tag": "qwen2.5-coder:7b", "gb": 4.7, "roles": ["coder"], "tier": "standard"},
    {"tag": "qwen3.6:27b", "gb": 17.0, "roles": ["tutor", "solver", "coder", "vision"], "tier": "experimental"},
    {"tag": "qwen3-embedding:0.6b", "gb": 0.64, "roles": ["embedding"], "tier": "any"},
]


def _sysctl(name: str) -> str | None:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=2).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def on_battery() -> bool | None:
    try:
        b = psutil.sensors_battery()
    except (AttributeError, NotImplementedError):
        return None
    return None if b is None else not b.power_plugged


def detect() -> Hardware:
    chip = _sysctl("machdep.cpu.brand_string") or platform.processor() or "unknown"
    vm = psutil.virtual_memory()
    return Hardware(
        chip=chip,
        ram_gb=round(vm.total / GB, 1),
        apple_silicon=platform.machine() == "arm64" and platform.system() == "Darwin",
        available_gb=round(vm.available / GB, 1),
        on_battery=on_battery(),
    )


def preset_for(ram_gb: float) -> str:
    if ram_gb < 20:
        return "light"
    if ram_gb < 48:
        return "standard"
    return "full"


def recommend(hw: Hardware) -> dict:
    """Preset and models that fit in about half the RAM."""
    budget = hw.ram_gb / 2
    preset = preset_for(hw.ram_gb)
    fits = [m for m in CATALOGUE if m["gb"] <= budget and m["tier"] != "experimental"]
    main = "qwen3.5:4b" if preset == "light" else "qwen3.5:9b"
    models = {r: main for r in ("tutor", "solver", "coder", "vision")}
    if preset != "light":
        models["coder"] = "qwen2.5-coder:7b"  # clear win on the code benchmark; swapped in only for code turns
    models["embedding"] = "qwen3-embedding:0.6b"
    experimental = [m["tag"] for m in CATALOGUE if m["tier"] == "experimental" and m["gb"] <= hw.ram_gb * 0.75]
    return {
        "preset": preset,
        "budget_gb": round(budget, 1),
        "models": models,
        "candidates": [m["tag"] for m in fits],
        "experimental": experimental,
        "notes": _notes(hw, preset),
    }


def _notes(hw: Hardware, preset: str) -> list[str]:
    notes = []
    if not hw.apple_silicon:
        notes.append("Not Apple Silicon: local models will be slow; consider the Light preset.")
    if preset == "light":
        notes.append("Light preset: one small model for every role; the hidden solver runs only on demand.")
    if preset == "standard":
        notes.append("Standard preset: one ~9-14B model fills tutor and solver; vision loads only during ingestion.")
    if hw.chip and "M5" in hw.chip and "Pro" not in hw.chip and "Max" not in hw.chip:
        notes.append("sysctl reports the chip without a Pro/Max suffix; macOS may abbreviate it. RAM is what drives the recommendation.")
    return notes


def memory_preflight(model_gb: float, min_free_after_gb: float = 3.0) -> dict:
    """Is there room to load a model of `model_gb` without swapping?"""
    vm = psutil.virtual_memory()
    available = vm.available / GB
    ok = available - model_gb >= min_free_after_gb
    return {"ok": ok, "available_gb": round(available, 1), "needed_gb": round(model_gb + min_free_after_gb, 1)}


def as_dict(hw: Hardware) -> dict:
    return asdict(hw)
