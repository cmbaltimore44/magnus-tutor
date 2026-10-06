# Magnus Tutor

A free, local, Socratic study tutor that lives alongside [Magnus](../magnus).
Ollama runs the models; nothing leaves the machine unless the optional cloud
provider is turned on. See `magnus-tutor-plan.md` for the full plan.

## Quick start

```sh
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/tutor init          # config folders, prompts, profile template
.venv/bin/tutor hardware      # chip, RAM, recommended preset and models
.venv/bin/tutor start         # backend + Ollama (if needed), opens the web app
.venv/bin/tutor chat -c em    # terminal chat with your profile and course as context
.venv/bin/tutor stop          # stop everything and unload models
.venv/bin/tutor doctor        # prove nothing is left running
```
