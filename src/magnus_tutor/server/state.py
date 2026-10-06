"""Shared backend state: settings, database, model manager, activity tracking."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from ..config import Paths, load_settings, paths
from ..db import DB, get_db
from ..llm.manager import ModelManager


@dataclass
class AppState:
    p: Paths
    settings: dict
    db: DB
    models: ModelManager
    last_activity: float = field(default_factory=time.time)
    background_tasks: set = field(default_factory=set)
    extras: dict = field(default_factory=dict)

    def touch(self) -> None:
        self.last_activity = time.time()

    def reload_settings(self) -> dict:
        self.settings = load_settings(self.p)
        self.models.reload(self.settings)
        return self.settings

    def spawn(self, coro) -> asyncio.Task:
        """Keep a reference to fire-and-forget tasks so they aren't collected."""
        t = asyncio.create_task(coro)
        self.background_tasks.add(t)
        t.add_done_callback(self.background_tasks.discard)
        return t


def make_state(p: Paths | None = None) -> AppState:
    p = p or paths()
    settings = load_settings(p)
    return AppState(p=p, settings=settings, db=get_db(p), models=ModelManager(settings))
