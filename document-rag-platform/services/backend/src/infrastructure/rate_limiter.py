"""Fail-closed request rate limiting.

Production uses an atomic Redis sliding window. The in-memory store exists
only for local development and deterministic unit tests; runtime validation
rejects it in staging/production.
"""

from __future__ import annotations

import inspect
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Deque, Protocol

from fastapi import HTTPException, Request, status

from src.config import settings


class RateLimitStore(Protocol):
    def allow(
        self, key: str, limit: int, window_seconds: float
    ) -> bool | Awaitable[bool]: ...


class SlidingWindowStore:
    """Process-local store intended only for local mode and tests."""

    def __init__(self, clock: Callable[[], float] | None = None):
        self._clock = clock or time.time
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._buckets[key]
            while hits and now - hits[0] >= window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def _purge_all(self) -> None:
        with self._lock:
            self._buckets.clear()


class RedisSlidingWindowStore:
    """Atomic Redis sorted-set sliding window with bounded key lifetime."""

    _ALLOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local cutoff = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then
  redis.call('EXPIRE', key, ttl)
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return 1
"""

    def __init__(self, redis_url: str, *, clock: Callable[[], float] | None = None):
        from redis.asyncio import from_url

        self._client = from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        self._clock = clock or time.time

    async def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = self._clock()
        member = f"{now:.9f}:{secrets.token_hex(8)}"
        result = await self._client.eval(
            self._ALLOW_SCRIPT,
            1,
            key,
            now,
            now - window_seconds,
            limit,
            member,
            max(1, int(window_seconds) + 1),
        )
        return bool(result)


def _cost_class(request: Request) -> str:
    path = request.url.path
    if path.endswith("/upload") or "/repositories" in path:
        return "ingestion"
    if "/chat/" in path:
        return "generation"
    if "/debug/" in path:
        return "debug"
    return "standard"


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path or request.url.path)


class RateLimiter:
    """FastAPI dependency keyed by principal, route template, and cost class."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_requests: int = 60,
        window_seconds: float = 60,
        key_prefix: str = "rl",
        store: RateLimitStore | None = None,
        clock: Callable[[], float] | None = None,
        key_fn: Callable[[Request], str] | None = None,
    ):
        self._enabled = enabled
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._key_prefix = key_prefix
        self._store = store or SlidingWindowStore(clock=clock)
        self._key_fn = key_fn or self._default_key

    @staticmethod
    def _default_key(request: Request) -> str:
        principal = getattr(request.state, "principal", None)
        principal_id = getattr(principal, "principal_id", None)
        if principal_id is None:
            host = request.client.host if request.client is not None else "unknown"
            principal_id = f"anonymous:{host}"
        return ":".join(
            (
                str(principal_id),
                request.method.upper(),
                _route_template(request),
                _cost_class(request),
            )
        )

    async def __call__(self, request: Request) -> None:
        if not self._enabled:
            return
        key = f"{self._key_prefix}:{self._key_fn(request)}"
        try:
            allowed = self._store.allow(
                key, self._max_requests, self._window_seconds
            )
            if inspect.isawaitable(allowed):
                allowed = await allowed
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limit service unavailable",
            ) from exc
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too Many Requests",
                headers={"Retry-After": str(int(self._window_seconds))},
            )

    @property
    def enabled(self) -> bool:
        return self._enabled


def build_rate_limiter() -> RateLimiter:
    store: RateLimitStore
    if settings.RATE_LIMIT_BACKEND == "redis":
        store = RedisSlidingWindowStore(settings.REDIS_URL)
    else:
        store = SlidingWindowStore()
    return RateLimiter(
        enabled=settings.RATE_LIMIT_ENABLED,
        max_requests=settings.RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        key_prefix=settings.RATE_LIMIT_KEY_PREFIX,
        store=store,
    )


rate_limiter = build_rate_limiter()
