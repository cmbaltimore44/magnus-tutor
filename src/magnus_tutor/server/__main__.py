"""`python -m magnus_tutor.server`: run the backend in the foreground (localhost only)."""

import uvicorn

from ..config import load_settings
from .app import create_app


def main() -> None:
    settings = load_settings()
    uvicorn.run(create_app(), host="127.0.0.1", port=settings["server"]["port"], log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
