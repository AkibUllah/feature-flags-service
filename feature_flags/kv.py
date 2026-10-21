"""Key-value backend used for the payload cache, usage counters and change notifications.

`RedisKV` is used when `REDIS_URL` is set and reachable; otherwise `MemoryKV` keeps a single
instance fully functional. Tests use `RedisKV(fakeredis.FakeRedis())`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

log = logging.getLogger(__name__)


class KV(Protocol):
    name: str

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None: ...
    def delete(self, *keys: str) -> None: ...
    def hincrby(self, key: str, field: str, amount: int = 1) -> int: ...
    def hset(self, key: str, field: str, value: str) -> None: ...
    def hgetall(self, key: str) -> dict[str, str]: ...
    def keys(self, pattern: str) -> list[str]: ...
    def ping(self) -> bool: ...


class MemoryKV:
    name = "memory"

    def __init__(self, clock=time.monotonic) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def _expired(self, key: str) -> bool:
        entry = self._data.get(key)
        if entry is None:
            return True
        _, expires = entry
        if expires is not None and self._clock() >= expires:
            del self._data[key]
            return True
        return False

    def get(self, key: str) -> str | None:
        with self._lock:
            if self._expired(key):
                return None
            return self._data[key][0]

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        with self._lock:
            expires = self._clock() + ttl_seconds if ttl_seconds else None
            self._data[key] = (value, expires)

    def delete(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._data.pop(key, None)
                self._hashes.pop(key, None)

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        with self._lock:
            bucket = self._hashes.setdefault(key, {})
            bucket[field] = str(int(bucket.get(field, "0")) + amount)
            return int(bucket[field])

    def hset(self, key: str, field: str, value: str) -> None:
        with self._lock:
            self._hashes.setdefault(key, {})[field] = value

    def hgetall(self, key: str) -> dict[str, str]:
        with self._lock:
            return dict(self._hashes.get(key, {}))

    def keys(self, pattern: str) -> list[str]:
        import fnmatch

        with self._lock:
            names = set(self._data) | set(self._hashes)
            return sorted(k for k in names if fnmatch.fnmatchcase(k, pattern))

    def ping(self) -> bool:
        return True


class RedisKV:
    name = "redis"

    def __init__(self, client) -> None:
        self._r = client

    @staticmethod
    def _s(value) -> str | None:
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    def get(self, key: str) -> str | None:
        return self._s(self._r.get(key))

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        self._r.set(key, value, ex=ttl_seconds)

    def delete(self, *keys: str) -> None:
        if keys:
            self._r.delete(*keys)

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        return int(self._r.hincrby(key, field, amount))

    def hset(self, key: str, field: str, value: str) -> None:
        self._r.hset(key, field, value)

    def hgetall(self, key: str) -> dict[str, str]:
        return {self._s(k): self._s(v) for k, v in self._r.hgetall(key).items()}

    def keys(self, pattern: str) -> list[str]:
        return sorted(self._s(k) for k in self._r.scan_iter(match=pattern))

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:  # pragma: no cover - network failure path
            return False


def build_kv(redis_url: str | None) -> KV:
    if not redis_url:
        return MemoryKV()
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return RedisKV(client)
    except Exception as exc:  # pragma: no cover - depends on a live Redis
        log.warning("Redis unavailable (%s); falling back to in-memory cache", exc)
        return MemoryKV()
