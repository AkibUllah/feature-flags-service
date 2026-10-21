from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from feature_flags.core.clauses import Context
from feature_flags.core.evaluate import EvalResult, evaluate

log = logging.getLogger(__name__)


class FlagClientError(RuntimeError):
    pass


class FlagClient:
    """Fetches `GET /v1/sdk/flags` with a server SDK key and evaluates flags locally.

    - `refresh()` sends `If-None-Match`; a 304 keeps the current payload.
    - `start_polling()` refreshes on a background thread every `poll_interval` seconds.
    - A failed refresh keeps serving the last good payload (and logs), it never raises
      into your request path. `variation()` returns `default` when nothing is loaded.
    """

    def __init__(
        self,
        sdk_key: str,
        base_url: str,
        *,
        poll_interval: float = 30.0,
        timeout: float = 5.0,
        http: httpx.Client | None = None,
        on_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not sdk_key.startswith("srv-"):
            raise FlagClientError("FlagClient needs a server SDK key (srv-…)")
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self._http = http or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {sdk_key}", "Accept": "application/json"}
        self._payload: dict[str, Any] | None = None
        self._etag: str | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_change = on_change
        self.last_error: str | None = None
        self.refresh_count = 0

    # --- lifecycle -------------------------------------------------------------------------

    def refresh(self) -> bool:
        """Fetch the payload. Returns True when a new payload was installed."""
        headers = dict(self._headers)
        if self._etag:
            headers["If-None-Match"] = self._etag
        try:
            response = self._http.get(f"{self.base_url}/v1/sdk/flags", headers=headers)
        except httpx.HTTPError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("flag refresh failed: %s", self.last_error)
            return False
        self.refresh_count += 1
        if response.status_code == 304:
            self.last_error = None
            return False
        if response.status_code != 200:
            self.last_error = f"HTTP {response.status_code}"
            log.warning("flag refresh failed: %s", self.last_error)
            return False
        with self._lock:
            self._payload = response.json()
            self._etag = response.headers.get("ETag")
        self.last_error = None
        if self._on_change:
            self._on_change(self._payload)
        return True

    def start_polling(self) -> None:
        if self._thread is not None:
            return
        self.refresh()
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="flagclient-poll", daemon=True)
        self._thread.start()

    def _poll_loop(self) -> None:  # pragma: no cover - timing dependent
        while not self._stop.wait(self.poll_interval):
            self.refresh()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval + 1)
            self._thread = None
        self._http.close()

    def __enter__(self) -> FlagClient:
        self.start_polling()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- evaluation ------------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self._payload is not None

    @property
    def version(self) -> int | None:
        return self._payload["version"] if self._payload else None

    def evaluate(self, flag_key: str, context: Context | dict[str, Any] | str) -> EvalResult | None:
        """Full result (value + reason); None if the flag is unknown or nothing is loaded."""
        ctx = _as_context(context)
        with self._lock:
            if self._payload is None:
                return None
            flag = self._payload["flags"].get(flag_key)
            if flag is None:
                return None
            return evaluate(flag, ctx, self._payload.get("segments", {}))

    def variation(
        self, flag_key: str, context: Context | dict[str, Any] | str, default: Any
    ) -> Any:
        result = self.evaluate(flag_key, context)
        if result is None or result.reason.kind == "ERROR":
            return default
        return result.value

    def is_enabled(
        self, flag_key: str, context: Context | dict[str, Any] | str, default: bool = False
    ) -> bool:
        return bool(self.variation(flag_key, context, default))

    def all_flags(self, context: Context | dict[str, Any] | str) -> dict[str, Any]:
        ctx = _as_context(context)
        with self._lock:
            if self._payload is None:
                return {}
            segments = self._payload.get("segments", {})
            return {
                key: evaluate(flag, ctx, segments).value
                for key, flag in self._payload["flags"].items()
            }

    def wait_until_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready:
                return True
            time.sleep(0.01)
        return self.ready


def _as_context(value: Context | dict[str, Any] | str) -> Context:
    if isinstance(value, Context):
        return value
    if isinstance(value, str):
        return Context(value)
    attributes = {k: v for k, v in value.items() if k != "key"}
    return Context(str(value["key"]), attributes)
