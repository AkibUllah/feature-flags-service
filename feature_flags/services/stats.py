"""Per-flag usage counters kept in the KV store (Redis or memory)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from feature_flags.kv import KV

PREFIX = "stats"


def _key(environment_id: str, flag_key: str) -> str:
    return f"{PREFIX}:{environment_id}:{flag_key}"


def record(
    kv: KV, environment_id: str, flag_key: str, variation: str | None, reason: str, now: datetime
) -> None:
    key = _key(environment_id, flag_key)
    kv.hincrby(key, "count", 1)
    kv.hincrby(key, f"variation:{variation}", 1)
    kv.hincrby(key, f"reason:{reason}", 1)
    kv.hset(key, "last_evaluated_at", now.isoformat())


def read(kv: KV, environment_id: str, flag_key: str) -> dict[str, Any]:
    raw = kv.hgetall(_key(environment_id, flag_key))
    return {
        "count": int(raw.get("count", 0)),
        "last_evaluated_at": raw.get("last_evaluated_at"),
        "variations": {
            k.split(":", 1)[1]: int(v) for k, v in raw.items() if k.startswith("variation:")
        },
        "reasons": {k.split(":", 1)[1]: int(v) for k, v in raw.items() if k.startswith("reason:")},
    }


def reset(kv: KV, environment_id: str, flag_key: str) -> None:
    kv.delete(_key(environment_id, flag_key))
