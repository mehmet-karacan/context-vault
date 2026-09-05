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
- :class:`ReadinessChecker` and :func:`build_default_readiness_checks`: required
  migration/DB/Redis/MinIO/queue/provider admission plus truthful optional
  provider capability degradation. Checkers are injectable and never expose
  dependency connection details in the public response.

Sensitive-content note (AKTIF_GOREV.md §9.4 "Hassas içerik ve full document
text loglama"): structured records must NOT include full document text or
raw user/secret content. The default ``log_structured`` helper filters to the
documented field set and never logs free text payloads; callers must supply
only identifiers/counters/latencies, never content.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Any, Callable, Deque, Dict, Iterator, Optional, TypeVar

from src.domain.clock import utc_now

# Only these bounded fields can reach JSON logs. Raw identifiers are converted
# to stable one-way hashes; content-shaped fields are not in this allow-list.
STRUCTURED_FIELDS: tuple = (
    "request_id",
    "trace_id",
    "span_id",
    "operation",
    "principal_hash",
    "workspace_hash",
    "project_hash",
    "document_hash",
    "version_hash",
    "job_hash",
    "work_item_hash",
    "attempt",
    "method",
    "route",
    "status",
    "project_id",
    "document_id",
    "version_id",
    "job_id",
    "principal_id",
    "workspace_id",
    "work_item_id",
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

IDENTIFIER_FIELDS = {
    "principal_id": "principal_hash",
    "workspace_id": "workspace_hash",
    "project_id": "project_hash",
    "document_id": "document_hash",
    "version_id": "version_hash",
    "job_id": "job_hash",
    "work_item_id": "work_item_hash",
    "query_id": "query_hash",
}
SAFE_OUTPUT_FIELDS = set(STRUCTURED_FIELDS) - set(IDENTIFIER_FIELDS)
SAFE_OUTPUT_FIELDS.add("query_hash")
SAFE_MESSAGES = frozenset(
    {
        "Unhandled exception",
        "job stage done",
        "operation failed",
        "request completed",
        "request failed",
        "retrieval query index miss",
        "retrieval stage completed",
        "retrieval stage slow",
        "retrieval_debug_access",
        "safe event",
        "shutdown started",
    }
)

_service_name = "backend"
_environment_name = os.getenv("APP_ENV", "unknown").strip().lower() or "unknown"

_logger = logging.getLogger("app.observability")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)
span_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("span_id", default="")

_LOWER_HEX = re.compile(r"[0-9a-f]+\Z")
_FUTURE_TRACE_FIELD = re.compile(r"[0-9a-z]+\Z")
_MAX_TRACEPARENT_BYTES = 512
trace_flags_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_flags", default="01"
)


def parse_traceparent(value: str | None) -> tuple[str, str, str] | None:
    """Parse a bounded W3C ``traceparent`` without normalizing bad input."""
    if not value or len(value) < 55 or len(value) > _MAX_TRACEPARENT_BYTES:
        return None
    parts = value.split("-")
    if len(parts) < 4 or (parts[0] == "00" and len(parts) != 4):
        return None
    version, trace_id, span_id, flags, *future = parts
    if (
        len(version) != 2
        or not _LOWER_HEX.fullmatch(version)
        or version == "ff"
        or len(trace_id) != 32
        or not _LOWER_HEX.fullmatch(trace_id)
        or len(span_id) != 16
        or not _LOWER_HEX.fullmatch(span_id)
        or len(flags) != 2
        or not _LOWER_HEX.fullmatch(flags)
        or any(not _FUTURE_TRACE_FIELD.fullmatch(part) for part in future)
    ):
        return None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id, span_id, flags


def canonical_traceparent(value: Any) -> str | None:
    """Return only the validated W3C base fields, never an untrusted payload."""
    if not isinstance(value, str):
        return None
    parsed = parse_traceparent(value)
    if parsed is None:
        return None
    return f"00-{parsed[0]}-{parsed[1]}-{parsed[2]}"


def current_traceparent() -> str | None:
    """Return a content-free W3C carrier for the current span, if any."""
    trace_id = trace_id_var.get()
    span_id = span_id_var.get()
    if not trace_id or not span_id:
        return None
    return f"00-{trace_id}-{span_id}-{trace_flags_var.get()}"


@contextmanager
def continue_trace(traceparent: str | None) -> Iterator[None]:
    """Bind a validated remote/durable parent while creating local child spans."""
    parsed = parse_traceparent(traceparent)
    trace_token = trace_id_var.set(parsed[0] if parsed else "")
    span_token = span_id_var.set(parsed[1] if parsed else "")
    flags_token = trace_flags_var.set(parsed[2] if parsed else "01")
    try:
        yield
    finally:
        trace_flags_var.reset(flags_token)
        span_id_var.reset(span_token)
        trace_id_var.reset(trace_token)


def set_observability_context(*, service: str, environment: str) -> None:
    """Set bounded deployment metadata included on every structured record."""
    global _service_name, _environment_name
    _service_name = service.strip()[:32] or "backend"
    _environment_name = environment.strip().lower()[:32] or "unknown"


def hash_identifier(value: Any) -> str:
    """Return a stable correlation token without exposing the raw identifier."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


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
        rendered_message = record.getMessage()
        payload: Dict[str, Any] = {
            "timestamp": utc_now().isoformat(),
            "level": record.levelname,
            "service": _service_name,
            "environment": _environment_name,
            "logger": record.name,
            "message": (
                rendered_message
                if rendered_message in SAFE_MESSAGES
                else "redacted log event"
            ),
        }
        rid = getattr(record, "request_id", None) or get_request_id()
        if rid:
            payload["request_id"] = rid
        trace_id = getattr(record, "trace_id", None) or trace_id_var.get()
        if trace_id:
            payload["trace_id"] = trace_id
        span_id = getattr(record, "span_id", None) or span_id_var.get()
        if span_id:
            payload["span_id"] = span_id
        for field, output_field in IDENTIFIER_FIELDS.items():
            value = getattr(record, field, None)
            if value is not None:
                payload[output_field] = hash_identifier(value)
        for field in SAFE_OUTPUT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields and isinstance(extra_fields, dict):
            for key, value in extra_fields.items():
                if value is None:
                    continue
                if key in IDENTIFIER_FIELDS:
                    payload[IDENTIFIER_FIELDS[key]] = hash_identifier(value)
                elif key in SAFE_OUTPUT_FIELDS and key not in payload:
                    payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO, propagate: bool = True) -> None:
    """Install a structured JSON handler on the root logger.

    Safe to call repeatedly (no duplicate handlers are added). Application
    loggers propagate to this single root handler by default.
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
    if message not in SAFE_MESSAGES:
        message = "redacted log event"
    extra = {"request_id": fields.pop("request_id", None) or get_request_id()}
    extra_fields: Dict[str, Any] = {}
    for key, value in fields.items():
        if key in STRUCTURED_FIELDS and value is not None:
            extra_fields[key] = value
    _logger.log(level, message, extra={"extra_fields": extra_fields, **extra})


ALLOWED_COUNTERS = frozenset(
    {
        "http.requests",
        "http.errors",
        "readiness.checks",
        "readiness.failures",
        "readiness.degraded",
        "queue.depth",
        "queue.oldest_age",
        "job.failures",
        "job.retries",
        "orphan.objects",
        "lease.stale",
        "outbox.backlog",
        "outbox.published",
        "outbox.failures",
        "retrieval.candidates",
        "retrieval.no_answer",
        "provider.calls",
        "provider.errors",
        "provider.tokens",
        "provider.cost_units",
        "citation.invalid",
        "citation.unsupported_claim",
        "db.pool.checked_out",
        "db.slow_queries",
        "backup.age_seconds",
        "restore_drill.age_seconds",
        "embedding.calls",
        "embedding.retries",
        "embedding.cache_hits",
        "trace.spans",
        "trace.errors",
        "shutdown.started",
    }
)

TRACE_OPERATIONS = frozenset(
    {
        "http.request",
        "outbox.dispatch",
        "ingestion.process",
        "provider.embedding",
        "provider.chat",
        "retrieval.pipeline",
        "answer.validation",
    }
)

ALLOWED_DURATIONS = frozenset(
    {
        "http.request",
        "job.stage",
        "provider.call",
        "db.query",
        "embedding.call",
        "retrieval.dense",
        "retrieval.lexical",
        "retrieval.identifier",
        "retrieval.fusion",
        "retrieval.rerank",
        "retrieval.context",
        *(f"trace.{operation}" for operation in TRACE_OPERATIONS),
    }
)


class MetricsCollector:
    """Thread-safe in-memory counters for Aşama 9.4 operational metrics
    (embedding calls / retries / cache-hits, job stage durations)."""

    def __init__(self) -> None:
        self._counters: Dict[str, int] = defaultdict(int)
        self._durations: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=1024)
        )
        self._gauges: Dict[str, float] = {}
        self._lock = threading.Lock()

    def incr(self, name: str, value: int = 1) -> None:
        if name not in ALLOWED_COUNTERS:
            raise ValueError(f"metric name is not allow-listed: {name}")
        with self._lock:
            self._counters[name] += value

    def record_duration(self, name: str, seconds: float) -> None:
        if name not in ALLOWED_DURATIONS:
            raise ValueError(f"metric name is not allow-listed: {name}")
        with self._lock:
            self._durations[name].append(seconds)

    def set_gauge(self, name: str, value: float) -> None:
        if name not in ALLOWED_COUNTERS:
            raise ValueError(f"metric name is not allow-listed: {name}")
        with self._lock:
            self._gauges[name] = float(value)

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
            gauges = dict(self._gauges)
            durations = {
                name: {
                    "count": len(values),
                    "total_ms": round(sum(values) * 1000, 3),
                    "mean_ms": round((sum(values) / len(values)) * 1000, 3)
                    if values
                    else 0.0,
                }
                for name, values in self._durations.items()
            }
        return {"counters": counts, "gauges": gauges, "durations": durations}


metrics = MetricsCollector()


def record_provider_usage(response: Any) -> None:
    """Record aggregate provider usage without model, prompt or response data."""
    usage = getattr(response, "usage", None)
    total_tokens = getattr(usage, "total_tokens", 0) if usage is not None else 0
    if isinstance(total_tokens, int) and total_tokens > 0:
        metrics.incr("provider.tokens", total_tokens)


_shutdown_started = threading.Event()


def begin_shutdown(service: str = "backend") -> None:
    """Close readiness admission without mutating or releasing active leases."""
    if not _shutdown_started.is_set():
        metrics.incr("shutdown.started")
        log_structured(logging.INFO, "shutdown started", operation="shutdown")
    _shutdown_started.set()


def reset_shutdown() -> None:
    """Open readiness admission after a fresh process/application startup."""
    _shutdown_started.clear()


@contextmanager
def trace_operation(operation: str) -> Iterator[None]:
    """Emit a bounded, content-free in-process span with W3C-sized ids."""
    if operation not in TRACE_OPERATIONS:
        raise ValueError(f"trace operation is not allow-listed: {operation}")
    trace_token = None
    if not trace_id_var.get():
        trace_token = trace_id_var.set(uuid.uuid4().hex)
    span_token = span_id_var.set(uuid.uuid4().hex[:16])
    started = time.perf_counter()
    metrics.incr("trace.spans")
    if operation.startswith("provider."):
        metrics.incr("provider.calls")
    try:
        yield
    except Exception:
        metrics.incr("trace.errors")
        if operation.startswith("provider."):
            metrics.incr("provider.errors")
        elif operation == "ingestion.process":
            metrics.incr("job.failures")
        elif operation == "outbox.dispatch":
            metrics.incr("outbox.failures")
        log_structured(
            logging.ERROR,
            "operation failed",
            operation=operation,
            error_code="operation_failed",
        )
        raise
    finally:
        elapsed = time.perf_counter() - started
        if operation.startswith("provider."):
            metrics.record_duration("provider.call", elapsed)
        metrics.record_duration(f"trace.{operation}", elapsed)
        span_id_var.reset(span_token)
        if trace_token is not None:
            trace_id_var.reset(trace_token)


F = TypeVar("F", bound=Callable[..., Any])


def traced(operation: str) -> Callable[[F], F]:
    """Decorator form of :func:`trace_operation` for synchronous boundaries."""

    def decorate(function: F) -> F:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            with trace_operation(operation):
                return function(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorate


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

        request_token = request_id_var.set(uuid.uuid4().hex)
        inbound_traceparents: list[str] = []
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() == b"traceparent":
                try:
                    inbound_traceparents.append(raw_value.decode("ascii"))
                except UnicodeDecodeError:
                    inbound_traceparents.append("")
        inbound_traceparent = (
            inbound_traceparents[0] if len(inbound_traceparents) == 1 else None
        )
        try:
            with continue_trace(inbound_traceparent), trace_operation("http.request"):
                start = time.perf_counter()
                status: Dict[str, int] = {"code": 500}
                error_code: Optional[str] = None

                async def send_wrapper(message: Dict[str, Any]) -> None:
                    if message["type"] == "http.response.start":
                        status["code"] = message["status"]
                        headers = list(message.get("headers", []))
                        headers.append(
                            (b"x-request-id", get_request_id().encode("ascii"))
                        )
                        headers.append(
                            (
                                b"traceparent",
                                f"00-{trace_id_var.get()}-{span_id_var.get()}-01".encode(
                                    "ascii"
                                ),
                            )
                        )
                        message = {**message, "headers": headers}
                    await send(message)

                metrics.incr("http.requests")
                try:
                    await self.app(scope, receive, send_wrapper)
                except Exception as exc:  # re-raise after safe metadata logging
                    metrics.incr("http.errors")
                    error_code = type(exc).__name__
                    raise
                finally:
                    latency = time.perf_counter() - start
                    metrics.record_duration("http.request", latency)
                    route = getattr(scope.get("route"), "path", None) or "unmatched"
                    log_structured(
                        logging.ERROR if error_code else logging.INFO,
                        "request failed" if error_code else "request completed",
                        method=scope.get("method"),
                        route=route,
                        status=status["code"],
                        latency_ms=round(latency * 1000, 3),
                        error_code=error_code,
                    )
        finally:
            request_id_var.reset(request_token)


# --- Readiness (health vs readiness split) --------------------------------


class ReadinessChecker:
    """Evaluate required and capability-optional dependencies fail-closed."""

    def __init__(
        self,
        checks: Dict[str, Callable[[], bool]],
        *,
        optional: Optional[set[str]] = None,
    ):
        self._checks = dict(checks)
        self._optional = frozenset(optional or ())
        unknown = self._optional - self._checks.keys()
        if unknown:
            raise ValueError("optional readiness dependency is not registered")

    def run(self) -> Dict[str, Any]:
        dependencies: Dict[str, str] = {}
        capabilities: Dict[str, str] = {}
        required_down = _shutdown_started.is_set()
        any_down = required_down
        for name, check in self._checks.items():
            metrics.incr("readiness.checks")
            try:
                ok = bool(check())
            except Exception:
                ok = False
            dependencies[name] = "ok" if ok else "down"
            if not ok:
                metrics.incr("readiness.failures")
                any_down = True
                if name in self._optional:
                    capabilities[name] = "unavailable"
                else:
                    required_down = True
        if required_down:
            status = "not_ready"
        elif any_down:
            metrics.incr("readiness.degraded")
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "ready": not required_down,
            "dependencies": dependencies,
            "capabilities": capabilities,
        }


def build_default_readiness_checks(
    configuration: Any = None,
) -> Dict[str, Callable[[], bool]]:
    """Build bounded read-only checks for schema, DB, Redis, MinIO and queue.

    Provider reachability uses an authenticated metadata request, never a
    generation call. Construction itself performs no I/O.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    from ..config import settings
    from ..db import engine
    from ..migration_settings import EXPECTED_ALEMBIC_HEAD
    from .storage.minio_storage import _split_endpoint

    cfg = configuration or settings
    probe_engine = create_engine(
        cfg.DATABASE_URL,
        poolclass=NullPool,
        connect_args={"connect_timeout": 2},
    )

    def db_check() -> bool:
        started = time.perf_counter()
        with probe_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        metrics.record_duration("db.query", time.perf_counter() - started)
        checked_out = getattr(engine.pool, "checkedout", None)
        if callable(checked_out):
            metrics.set_gauge("db.pool.checked_out", checked_out())
        return True

    def migration_check() -> bool:
        started = time.perf_counter()
        with probe_engine.connect() as conn:
            current = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
        metrics.record_duration("db.query", time.perf_counter() - started)
        return current == EXPECTED_ALEMBIC_HEAD

    def redis_check() -> bool:
        try:
            import redis  # local import: redis is optional at import time

            client = redis.from_url(
                cfg.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
            )
            try:
                return bool(client.ping())
            finally:
                client.close()
        except Exception:
            return False

    def minio_check() -> bool:
        from minio import Minio
        from urllib3 import PoolManager, Timeout

        host_port, secure = _split_endpoint(cfg.MINIO_ENDPOINT)
        pool = PoolManager(timeout=Timeout(connect=2, read=2), retries=False)
        client = Minio(
            host_port,
            access_key=cfg.MINIO_ACCESS_KEY,
            secret_key=cfg.MINIO_SECRET_KEY,
            secure=secure,
            http_client=pool,
        )
        try:
            return bool(client.bucket_exists(cfg.MINIO_BUCKET))
        finally:
            pool.clear()

    def queue_check() -> bool:
        try:
            from kombu import Connection
            import redis

            connection = Connection(cfg.REDIS_URL, connect_timeout=2)
            try:
                connection.ensure_connection(max_retries=0)
                queue = redis.from_url(
                    cfg.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
                )
                try:
                    metrics.set_gauge("queue.depth", queue.llen("celery"))
                finally:
                    queue.close()
                return bool(connection.connected)
            finally:
                connection.release()
        except Exception:
            return False

    def provider_check() -> bool:
        # Lightweight probe of the LLM/embedding gateway base URL. A HEAD is
        # preferred over a real embedding call (which would be expensive and
        # could log sensitive content). Timeout keeps a dead gateway from
        # stalling readiness.
        try:
            import urllib.request

            req = urllib.request.Request(
                cfg.LITELLM_BASE_URL.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {cfg.LITELLM_API_KEY}"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2):
                return True
        except Exception:
            return False

    return {
        "migration": migration_check,
        "db": db_check,
        "redis": redis_check,
        "minio": minio_check,
        "queue": queue_check,
        "provider": provider_check,
    }
