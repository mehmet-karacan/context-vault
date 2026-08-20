"""Observability infrastructure (Aşama 9.4).

Provides:

- :class:`StructuredJsonFormatter`: a stdlib ``logging`` formatter that renders
  each record as a single JSON line carrying the documented structured-log
  field set plus ``request_id``.
- :class:`RequestContextMiddleware`: a pure-ASGI middleware that tags every
  HTTP request with a generated ``request_id`` (a ``contextvars.ContextVar`` so
  it threads through async task boundaries spawned during the request) and logs
  a completion record (method / path / status / latency_ms / error_code).
- helper functions (:func:`get_request_id`, :func:`set_request_id`,
  :func:`log_structured`) that ingestion / embedding / retrieval code can call
  to emit Aşama 9.4 records (job stage durations, embedding call/retry/cache-hit
  counters).
- :class:`ReadinessChecker` and :func:`build_default_readiness_checks`: split
  health-vs-readiness, with per-dependency health for DB / Redis / MinIO /
  embedding gateway. Checkers are injectable so readiness is testable without
  real services, and a single dependency being down degrades (never crashes)
  the result.

Sensitive-content note (AKTIF_GOREV.md §9.4 "Hassas içerik ve full document
text loglama"): structured records must NOT include full document text or
raw user/secret content. The default ``log_structured`` helper filters to the
documented field set and never logs free text payloads; callers must supply
only identifiers/counters/latencies, never content.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# The canonical Aşama 9.4 structured-log field set (AKTIF_GOREV.md §9.4).
STRUCTURED_FIELDS: tuple = (
    "request_id",
    "project_id",
    "document_id",
    "version_id",
    "job_id",
    "parser",
    "ocr_engine",
    "chunker_profile",
    "embedding_profile",
    "query_id",
    "retrieval_stage",
    "candidate_count",
    "latency_ms",
    "error_code",
)

_logger = logging.getLogger("app.observability")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def get_request_id() -> str:
    """Return the request_id bound on the current context ("" outside a
    request)."""
    return request_id_var.get()


def set_request_id(request_id: Optional[str] = None) -> str:
    """Bind ``request_id`` to the current context and return it. If none is
    given a fresh uuid is generated."""
    rid = request_id or uuid.uuid4().hex
    request_id_var.set(rid)
    return rid


class StructuredJsonFormatter(logging.Formatter):
    """Logging formatter that serializes each record to a single JSON object.

    Always includes ``timestamp`` / ``level`` / ``logger`` / ``message`` plus
    the current ``request_id``. Any of the ``STRUCTURED_FIELDS`` present as
    attributes on the record (via logging ``extra``) are merged in, as are any
    extra fields supplied under ``extra_fields``. Content/secret-free by
    design.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = getattr(record, "request_id", None) or get_request_id()
        if rid:
            payload["request_id"] = rid
        for field in STRUCTURED_FIELDS:
            if field == "request_id":
                continue
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields and isinstance(extra_fields, dict):
            for key, value in extra_fields.items():
                if key not in payload:
                    payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO, propagate: bool = False) -> None:
    """Install a structured JSON handler on the root logger.

    Safe to call repeatedly (no duplicate handlers are added). ``propagate``
    is kept False by default so structured records are emitted exactly once.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_structured", False):
            return
    handler = logging.StreamHandler()
    handler._structured = True  # type: ignore[attr-defined]
    handler.setFormatter(StructuredJsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("app.observability", "http.request", "app.structured"):
        logging.getLogger(name).propagate = propagate


def log_structured(level: int, message: str, **fields: Any) -> None:
    """Emit a structured record carrying only the documented Aşama 9.4 field
    set plus any allowed identifiers/counters.

    Extra keyword args are filtered to ``STRUCTURED_FIELDS`` so accidental
    free-text/secret content cannot be unintentionally logged; callers pass a
    clear (non-sensitive) ``message``.
    """
    extra = {"request_id": fields.pop("request_id", None) or get_request_id()}
    extra_fields: Dict[str, Any] = {}
    for key, value in fields.items():
        if key in STRUCTURED_FIELDS and value is not None:
            extra_fields[key] = value
    _logger.log(level, message, extra={"extra_fields": extra_fields, **extra})


class MetricsCollector:
    """Thread-safe in-memory counters for Aşama 9.4 operational metrics
    (embedding calls / retries / cache-hits, job stage durations)."""

    def __init__(self) -> None:
        self._counters: Dict[str, int] = defaultdict(int)
        self._durations: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def incr(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def record_duration(self, name: str, seconds: float) -> None:
        with self._lock:
            self._durations[name].append(seconds)

    def record_embedding(
        self, *, calls: int = 0, retries: int = 0, cache_hits: int = 0
    ) -> None:
        if calls:
            self.incr("embedding.calls", calls)
        if retries:
            self.incr("embedding.retries", retries)
        if cache_hits:
            self.incr("embedding.cache_hits", cache_hits)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            counts = dict(self._counters)
            durations = {
                name: {
                    "count": len(values),
                    "total_ms": round(sum(values) * 1000, 3),
                    "mean_ms": round((sum(values) / len(values)) * 1000, 3) if values else 0.0,
                }
                for name, values in self._durations.items()
            }
        return {"counters": counts, "durations": durations}


metrics = MetricsCollector()


class RequestContextMiddleware:
    """Pure-ASGI middleware that tags each HTTP request with a request_id and
    logs a completion record.

    Because it is a plain ASGI callable it works reliably with FastAPI/Starlette
    regardless of ``BaseHTTPMiddleware`` buffering concerns. The ``request_id``
    is set on a ``contextvars.ContextVar`` so it propagates into any async
    subtasks the request spawns.
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        set_request_id()
        start = time.perf_counter()
        status: Dict[str, int] = {"code": 500}
        error_code: Optional[str] = None

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # re-raise after logging; app handler may catch
            error_code = type(exc).__name__
            lat = (time.perf_counter() - start) * 1000
            logging.getLogger("http.request").error(
                "request failed",
                extra={
                    "extra_fields": {
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status": status["code"],
                        "latency_ms": round(lat, 3),
                        "error_code": error_code,
                    }
                },
            )
            raise
        finally:
            if error_code is None:
                lat = (time.perf_counter() - start) * 1000
                logging.getLogger("http.request").info(
                    "request completed",
                    extra={
                        "extra_fields": {
                            "method": scope.get("method"),
                            "path": scope.get("path"),
                            "status": status["code"],
                            "latency_ms": round(lat, 3),
                        }
                    },
                )
            request_id_var.set("")


# --- Readiness (health vs readiness split) --------------------------------


class ReadinessChecker:
    """Runs a set of per-dependency health checks and reports ok/degraded.

    Never raises for a single failing dependency: each check is wrapped so a
    down dependency degrades (not crashes) the overall status
    (AKTIF_GOREV.md §9.4 dependency health for gateway/DB/Redis/MinIO).
    """

    def __init__(self, checks: Dict[str, Callable[[], bool]]):
        self._checks = dict(checks)

    def run(self) -> Dict[str, Any]:
        dependencies: Dict[str, str] = {}
        overall = "ok"
        for name, check in self._checks.items():
            try:
                ok = bool(check())
            except Exception:
                ok = False
            dependencies[name] = "ok" if ok else "down"
            if not ok:
                overall = "degraded"
        return {"status": overall, "dependencies": dependencies}


def build_default_readiness_checks() -> Dict[str, Callable[[], bool]]:
    """Build the production readiness checks (DB / Redis / MinIO / gateway).

    Each returns a bool and swallows its own exceptions. Nothing here makes a
    network call at construction time, so importing this module stays safe.
    """
    from sqlalchemy import text

    from ..config import settings
    from ..db import engine
    from .storage.minio_storage import _split_endpoint, MinioObjectStorage

    def db_check() -> bool:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    def redis_check() -> bool:
        try:
            import redis  # local import: redis is optional at import time

            client = redis.from_url(settings.REDIS_URL, socket_timeout=2)
            try:
                return bool(client.ping())
            finally:
                client.close()
        except Exception:
            return False

    def minio_check() -> bool:
        storage = MinioObjectStorage(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            bucket=settings.MINIO_BUCKET,
        )
        # bucket_exists is a cheap HEAD-style call; skip creation during a
        # health probe (readiness must be read-only).
        host_port, secure = _split_endpoint(settings.MINIO_ENDPOINT)
        return bool(storage._client.bucket_exists(storage._bucket))

    def gateway_check() -> bool:
        # Lightweight probe of the LLM/embedding gateway base URL. A HEAD is
        # preferred over a real embedding call (which would be expensive and
        # could log sensitive content). Timeout keeps a dead gateway from
        # stalling readiness.
        try:
            import urllib.request

            req = urllib.request.Request(
                settings.LITELLM_BASE_URL.rstrip("/") + "/models", method="GET"
            )
            with urllib.request.urlopen(req, timeout=2):
                return True
        except Exception:
            return False

    return {
        "db": db_check,
        "redis": redis_check,
        "minio": minio_check,
        "gateway": gateway_check,
    }
