"""In-process change notifications feeding the SSE stream.

Every environment has a list of subscriber queues. Publishing a new version fans the event
out to all of them without blocking. (Cross-instance fan-out through Redis pub/sub is on the
roadmap; one instance, or sticky sessions on a load balancer, is the supported topology.)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChangeEvent:
    environment_id: str
    version: int
    kind: str  # flag | segment | scheduled
    entity_key: str | None = None
    extra: dict[str, Any] | None = None


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[ChangeEvent]]] = defaultdict(list)

    @contextmanager
    def subscribe(self, environment_id: str):
        queue: asyncio.Queue[ChangeEvent] = asyncio.Queue(maxsize=256)
        self._subscribers[environment_id].append(queue)
        try:
            yield queue
        finally:
            self._subscribers[environment_id].remove(queue)

    def publish(self, event: ChangeEvent) -> int:
        delivered = 0
        for queue in list(self._subscribers.get(event.environment_id, [])):
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:  # slow consumer; it will resync on the next heartbeat
                continue
        return delivered

    def subscriber_count(self, environment_id: str | None = None) -> int:
        if environment_id is not None:
            return len(self._subscribers.get(environment_id, []))
        return sum(len(v) for v in self._subscribers.values())
