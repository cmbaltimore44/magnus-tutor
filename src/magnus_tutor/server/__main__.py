"""`python -m magnus_tutor.server`: run the backend in the foreground (localhost only)."""

import os

import uvicorn

from ..config import load_settings
from .app import create_app


def main() -> None:
    settings = load_settings()
    # MAGNUS_TUTOR_NO_MANAGE=1: don't start/stop Ollama or unload models (dev, benchmarks running).
    manage = os.environ.get("MAGNUS_TUTOR_NO_MANAGE") != "1"
    uvicorn.run(create_app(manage_processes=manage), host="127.0.0.1", port=settings["server"]["port"], log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
