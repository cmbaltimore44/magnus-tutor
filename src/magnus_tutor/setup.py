"""First-run setup: config folders, prompt files, profile template, settings from
the hardware check, and the default languages file. Never overwrites edits."""

from __future__ import annotations

from . import prompts
from .config import Paths, load_yaml, paths, write_yaml_atomic
from .courses import PROFILE_TEMPLATE
from .hardware import detect, recommend
from .languages import DEFAULT_LANGUAGES_YAML


def bootstrap(p: Paths | None = None) -> dict:
    p = p or paths()
    created = []
    for d in (p.config, p.courses, p.prompts, p.data, p.cache, p.run, p.uploads):
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
    installed = prompts.install_defaults(p)
    if not p.profile.exists():
        p.profile.write_text(PROFILE_TEMPLATE)
        created.append(str(p.profile))
    if not p.languages.exists():
        p.languages.write_text(DEFAULT_LANGUAGES_YAML)
        created.append(str(p.languages))
    rec = None
    current = load_yaml(p.settings, None)
    if current is None:
        hw = detect()
        rec = recommend(hw)
        write_yaml_atomic(p.settings, {"preset": rec["preset"], "models": rec["models"], "hardware": {"chip": hw.chip, "ram_gb": hw.ram_gb}})
        created.append(str(p.settings))
    return {"created": created, "prompts_installed": installed, "recommendation": rec}
