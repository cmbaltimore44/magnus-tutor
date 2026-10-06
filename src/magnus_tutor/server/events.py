"""In-process event bus pushed to browsers over one SSE stream (/api/events).

Events: timer (Magnus focus timer state), job (ingestion progress), solver
(hidden pass status for a problem), resources, settings.
"""

from __future__ import annotations

import asyncio
import json


class Bus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self.last: dict[str, dict] = {}  # latest event per type, replayed to new subscribers

    def publish(self, kind: str, data: dict) -> None:
        self.last[kind] = data
        for q in list(self._subs):
            try:
                q.put_nowait((kind, data))
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        for kind, data in self.last.items():
            q.put_nowait((kind, data))
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @property
    def subscribers(self) -> int:
        return len(self._subs)


def sse(kind: str, data) -> str:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


bus = Bus()
