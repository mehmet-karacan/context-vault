"""Dependency-light in-memory rate limiter (Aşama 9.5).

Provides:

- :class:`SlidingWindowStore` — thread-safe in-memory sliding-window counter
  keyed by a string, with an injectable clock so window-expiry behaviour is
  unit-testable without sleeping.
- :class:`RateLimiter` — a FastAPI dependency (``__call__``) that enforces a
  per-client-IP limit and raises ``HTTPException(429)`` once the window is
  exhausted. Enabled / limits are config driven; a pluggable store/clock makes
  it fully injectable/testable.

No Redis is required — the default backing store is in-memory (AKTIF_GOREV.md
§9.5 rate limiting; "Do NOT require redis; default in-memory is fine").
"""

import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, Optional

from fastapi import HTTPException, Request

from ..config import settings


class SlidingWindowStore:
    """In-memory sliding-window store.

    Tracks the timestamps of the last ``limit`` hits per key within
    ``window_seconds``. ``allow`` returns True (recording the hit) if the key
    has fewer than ``limit`` distinct timestamps strictly inside the window;
    old timestamps are pruned lazily on each call.
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._clock: Callable[[], float] = clock or time.time
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._buckets[key]
            # Prune hits that have fallen out of the window.
            while hits and now - hits[0] >= window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def _purge_all(self) -> None:
        with self._lock:
            self._buckets.clear()


class RateLimiter:
    """Config-driven, injectable FastAPI rate-limit dependency.

    Usage as a dependency::

        @router.post("/chat/query")
        def query(_: None = Depends(rate_limiter), ...):
            ...

    When disabled (``RATE_LIMIT_ENABLED=false``) it is a transparent no-op, so
    existing deployments are unaffected.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_requests: int = 60,
        window_seconds: float = 60,
        key_prefix: str = "rl",
        store: Optional[SlidingWindowStore] = None,
        clock: Optional[Callable[[], float]] = None,
        key_fn: Optional[Callable[[Request], str]] = None,
    ):
        self._enabled = enabled
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._key_prefix = key_prefix
        self._store = store or SlidingWindowStore(clock=clock)
        self._key_fn = key_fn or self._default_client_key

    @staticmethod
    def _default_client_key(request: Request) -> str:
        host = request.client.host if request.client is not None else "unknown"
        return host

    async def __call__(self, request: Request) -> None:
        if not self._enabled:
            return
        client_key = self._key_fn(request)
        key = f"{self._key_prefix}:{client_key}"
        if not self._store.allow(key, self._max_requests, self._window_seconds):
            raise HTTPException(status_code=429, detail="Too Many Requests")

    @property
    def enabled(self) -> bool:
        return self._enabled


# Module-level singleton wired from config; endpoints import this and add it as
# a dependency. Built lazily at import time — never performs network I/O.
rate_limiter = RateLimiter(
    enabled=settings.RATE_LIMIT_ENABLED,
    max_requests=settings.RATE_LIMIT_MAX_REQUESTS,
    window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    key_prefix=settings.RATE_LIMIT_KEY_PREFIX,
)
