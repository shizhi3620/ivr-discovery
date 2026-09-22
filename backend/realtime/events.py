"""Bounded local event fan-out for the realtime relay."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any


class EventBus:
    """Fan out relay events to localhost subscribers with sequence numbers."""

    def __init__(self, queue_size: int = 256):
        self._queue_size = queue_size
        self._next_sequence = 0
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=self._queue_size
        )
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        self._next_sequence += 1
        payload = {
            **event,
            "sequence": self._next_sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)
        return payload
