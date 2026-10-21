from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from feature_flags.clock import Clock, SystemClock
from feature_flags.config import Settings
from feature_flags.kv import KV, MemoryKV, build_kv
from feature_flags.services.bus import EventBus


@dataclass
class AppContext:
    settings: Settings
    kv: KV = field(default_factory=MemoryKV)
    clock: Clock = field(default_factory=SystemClock)
    bus: EventBus = field(default_factory=EventBus)
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=5))

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        kv: KV | None = None,
        clock: Clock | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> AppContext:
        return cls(
            settings=settings,
            kv=kv or build_kv(settings.redis_url),
            clock=clock or SystemClock(),
            bus=EventBus(),
            http=httpx.Client(timeout=settings.webhook_timeout_seconds, transport=transport),
        )
