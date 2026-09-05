"""Aşama 9.4-9.5 tests: structured logging + request_id threading, CORS /
debug / stack-trace hardening, and the rate limiter.

These are unit tests: the structured-logger tests run the formatter / middleware
directly, and the CORS / debug / rate-limit tests build throwaway apps so no
real database, Redis, MinIO or gateway is touched.
"""

import io
import json
import logging
import re
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Request
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.config import Settings
from src.infrastructure.observability import (
    ALLOWED_COUNTERS,
    ALLOWED_DURATIONS,
    ReadinessChecker,
    RequestContextMiddleware,
    StructuredJsonFormatter,
    begin_shutdown,
    build_default_readiness_checks,
    continue_trace,
    current_traceparent,
    get_request_id,
    metrics,
    parse_traceparent,
    record_provider_usage,
    reset_shutdown,
    set_request_id,
    trace_operation,
)
from src.infrastructure.rate_limiter import RateLimiter, SlidingWindowStore
from src.main import create_app


@pytest.fixture(autouse=True)
def _clean_shutdown_state():
    reset_shutdown()
    yield
    reset_shutdown()


# --- Structured logging / request_id --------------------------------------


def test_structured_formatter_emits_json_with_request_id_and_structured_fields():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredJsonFormatter())
    logger = logging.getLogger("test.str.obs")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info(
        "job stage done",
        extra={
            "request_id": "rid-abc-123",
            "job_id": "job-9",
            "parser": "docling",
            "latency_ms": 42,
        },
    )

    data = json.loads(stream.getvalue().strip())
    assert data["request_id"] == "rid-abc-123"
    assert data["job_hash"] != "job-9"
    assert re.fullmatch(r"[0-9a-f]{16}", data["job_hash"])
    assert data["parser"] == "docling"
    assert data["latency_ms"] == 42
    assert data["level"] == "INFO"
    assert data["message"] == "job stage done"
    assert data["service"] == "backend"
    assert data["environment"]


def test_structured_formatter_drops_sensitive_and_unknown_fields():
    record = logging.LogRecord(
        "app.structured",
        logging.INFO,
        __file__,
        1,
        "safe event",
        (),
        None,
    )
    record.extra_fields = {
        "operation": "answer.validate",
        "prompt": "private prompt",
        "context": "private context",
        "token": "secret-token",
        "email": "person@example.invalid",
    }
    payload = StructuredJsonFormatter().format(record)
    assert "private prompt" not in payload
    assert "private context" not in payload
    assert "secret-token" not in payload
    assert "person@example.invalid" not in payload
    assert json.loads(payload)["operation"] == "answer.validate"

    record.msg = "raw prompt content must never survive"
    assert json.loads(StructuredJsonFormatter().format(record))["message"] == (
        "redacted log event"
    )


def test_request_id_contextvar_threads_through_middleware():
    async def route(request):
        return JSONResponse({"rid": get_request_id()})

    app = Starlette(routes=[Route("/rid", route)])
    app.add_middleware(RequestContextMiddleware)

    with TestClient(app) as client:
        response = client.get("/rid")
        body = response.json()
        assert body["rid"]
        assert response.headers["x-request-id"] == body["rid"]
        assert re.fullmatch(
            r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", response.headers["traceparent"]
        )
        # A second request gets a different id.
        body2 = client.get("/rid").json()
        assert body2["rid"]
        assert body["rid"] != body2["rid"]


def test_middleware_continues_valid_w3c_trace_and_rejects_invalid_parent():
    async def route(_request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/trace", route)])
    app.add_middleware(RequestContextMiddleware)
    trace_id = "a" * 32
    parent = f"00-{trace_id}-{'b' * 16}-01"

    with TestClient(app) as client:
        continued = client.get("/trace", headers={"traceparent": parent})
        invalid = client.get("/trace", headers={"traceparent": "not-a-trace"})

    assert continued.headers["traceparent"].split("-")[1] == trace_id
    assert continued.headers["traceparent"].split("-")[2] != "b" * 16
    assert invalid.headers["traceparent"].split("-")[1] != trace_id


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "00-" + "0" * 32 + "-" + "1" * 16 + "-01",
        "00-" + "1" * 32 + "-" + "0" * 16 + "-01",
        "01-" + "1" * 32 + "-" + "2" * 16 + "-01",
        "00-" + "g" * 32 + "-" + "2" * 16 + "-01",
    ],
)
def test_traceparent_parser_rejects_invalid_or_unsafe_carriers(value):
    assert parse_traceparent(value) is None


def test_continue_trace_exposes_content_free_carrier_and_restores_context():
    parent = f"00-{'1' * 32}-{'2' * 16}-01"
    before = current_traceparent()
    with continue_trace(parent):
        assert current_traceparent() == parent
        with trace_operation("outbox.dispatch"):
            child = current_traceparent()
            assert child is not None
            assert child.split("-")[1] == "1" * 32
            assert child.split("-")[2] != "2" * 16
    assert current_traceparent() == before


def test_set_request_id_returns_and_defaults_to_uuid():
    assert set_request_id("custom")
    assert get_request_id() == "custom"
    rid = set_request_id()
    assert rid and get_request_id() == rid


def test_metrics_collector_records_embedding_counters():
    before = metrics.snapshot()["counters"].get("embedding.calls", 0)
    metrics.record_embedding(calls=3, retries=1, cache_hits=5)
    snapshot = metrics.snapshot()["counters"]
    assert snapshot["embedding.calls"] == before + 3
    assert snapshot["embedding.retries"] >= 1
    assert snapshot["embedding.cache_hits"] >= 5


def test_metrics_reject_high_cardinality_names():
    assert "queue.depth" in ALLOWED_COUNTERS
    assert "provider.call" in ALLOWED_DURATIONS
    with pytest.raises(ValueError, match="not allow-listed"):
        metrics.incr("job.550e8400-e29b-41d4-a716-446655440000.failure")
    with pytest.raises(ValueError, match="not allow-listed"):
        metrics.record_duration("retrieval.user-supplied-stage", 0.1)


def test_trace_and_provider_usage_are_content_free_aggregates():
    before = metrics.snapshot()["counters"]
    with trace_operation("provider.chat"):
        record_provider_usage(SimpleNamespace(usage=SimpleNamespace(total_tokens=7)))
    after = metrics.snapshot()["counters"]
    assert after["provider.calls"] == before.get("provider.calls", 0) + 1
    assert after["provider.tokens"] == before.get("provider.tokens", 0) + 7
    assert after["trace.spans"] == before.get("trace.spans", 0) + 1


def test_default_readiness_registers_every_required_dependency():
    assert set(build_default_readiness_checks()) == {
        "migration",
        "db",
        "redis",
        "minio",
        "queue",
        "provider",
    }


# --- Readiness (health vs readiness split) ---------------------------------


def test_readiness_ok_when_all_dependencies_up():
    checker = ReadinessChecker(
        {
            "db": lambda: True,
            "redis": lambda: True,
            "minio": lambda: True,
            "provider": lambda: True,
            "migration": lambda: True,
            "queue": lambda: True,
        }
    )
    result = checker.run()
    assert result["status"] == "ok"
    assert result["dependencies"] == {
        "db": "ok",
        "redis": "ok",
        "minio": "ok",
        "provider": "ok",
        "migration": "ok",
        "queue": "ok",
    }


def test_readiness_degraded_not_crash_when_dependency_down():
    checker = ReadinessChecker(
        {
            "db": lambda: True,
            "redis": lambda: False,
            "minio": lambda: True,
            "provider": lambda: True,
        }
    )
    result = checker.run()  # must not raise
    assert result["status"] == "not_ready"
    assert result["ready"] is False
    assert result["dependencies"]["redis"] == "down"
    assert result["dependencies"]["db"] == "ok"


def test_readiness_treats_raising_checker_as_down():
    def boom():
        raise RuntimeError("minio unreachable")

    checker = ReadinessChecker({"minio": boom, "db": lambda: True})
    result = checker.run()
    assert result["status"] == "not_ready"
    assert result["dependencies"]["minio"] == "down"
    assert result["dependencies"]["db"] == "ok"


def test_optional_provider_is_capability_degraded_but_ready():
    checker = ReadinessChecker(
        {"db": lambda: True, "provider": lambda: False},
        optional={"provider"},
    )
    result = checker.run()
    assert result["status"] == "degraded"
    assert result["ready"] is True
    assert result["capabilities"] == {"provider": "unavailable"}


def test_shutdown_closes_readiness_admission_without_releasing_leases():
    reset_shutdown()
    begin_shutdown("worker")
    try:
        result = ReadinessChecker({"db": lambda: True}).run()
        assert result["status"] == "not_ready"
        assert result["ready"] is False
    finally:
        reset_shutdown()


# --- CORS / debug / stack-trace hardening ----------------------------------


def _cors_origins(app) -> list:
    for m in app.user_middleware:
        if getattr(m, "cls", None).__name__ == "CORSMiddleware":
            return list(m.kwargs["allow_origins"])
    return []


def _secured_settings(app_env: str, **overrides):
    values = {
        "APP_ENV": app_env,
        "AUTH_MODE": "api_key",
        "API_KEY_PEPPER": "p" * 32,
        "DATABASE_URL": "postgresql://app:nondefault@db:5432/context_vault",
        "MINIO_ACCESS_KEY": "context-vault-app",
        "MINIO_SECRET_KEY": "non-default-storage-secret",
        "RATE_LIMIT_ENABLED": app_env in {"production", "staging"},
        "RATE_LIMIT_BACKEND": (
            "redis" if app_env in {"production", "staging"} else "memory"
        ),
    }
    values.update(overrides)
    return Settings(**values)


def test_cors_never_wildcard_in_production():
    prod_app = create_app(_secured_settings("production"))
    origins = _cors_origins(prod_app)
    assert "*" not in origins
    assert origins == []


def test_cors_defaults_to_dev_origins_in_development():
    dev_app = create_app(_secured_settings("development"))
    origins = _cors_origins(dev_app)
    assert "http://localhost:3000" in origins
    assert "*" not in origins


def test_cors_respects_explicit_allowlist():
    app = create_app(
        _secured_settings(
            "production",
            CORS_ALLOW_ORIGINS="https://a.example.com,https://b.example.com",
        )
    )
    origins = _cors_origins(app)
    assert "*" not in origins
    assert origins == ["https://a.example.com", "https://b.example.com"]


def _boom():
    raise RuntimeError("top-secret-stack-frame")


def test_generic_error_hides_stack_trace_when_not_debug(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)
    app = create_app(_secured_settings("development", API_DEBUG=False))
    app.add_api_route("/boom", _boom, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal Server Error"
    assert "top-secret-stack-frame" not in json.dumps(body)


def test_debug_error_includes_stack_details_when_api_debug_on(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)
    app = create_app(_secured_settings("development", API_DEBUG=True))
    app.add_api_route("/boom", _boom, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert "top-secret-stack-frame" in body["detail"]


# --- Rate limiter -----------------------------------------------------------


def test_rate_limiter_enforces_max_and_returns_429():
    limiter = RateLimiter(enabled=True, max_requests=3, window_seconds=60)

    app = FastAPI()

    @app.get("/limited")
    def limited(_: None = Depends(limiter)):
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 429


def test_rate_limiter_is_noop_when_disabled():
    limiter = RateLimiter(enabled=False, max_requests=1, window_seconds=60)

    app = FastAPI()

    @app.get("/limited")
    def limited(_: None = Depends(limiter)):
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 200


def test_rate_limiter_fails_closed_when_store_is_unavailable():
    class BrokenStore:
        async def allow(self, key, limit, window_seconds):
            raise ConnectionError("redis unavailable")

    limiter = RateLimiter(
        enabled=True, max_requests=1, window_seconds=60, store=BrokenStore()
    )
    app = FastAPI()

    @app.get("/limited")
    async def limited(_: None = Depends(limiter)):
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/limited")
    assert response.status_code == 503
    assert response.json() == {"detail": "Rate limit service unavailable"}


def test_rate_limit_key_contains_principal_route_and_cost_class():
    captured = []

    class CapturingStore:
        async def allow(self, key, limit, window_seconds):
            captured.append(key)
            return True

    async def identify(request: Request):
        request.state.principal = type("P", (), {"principal_id": "principal-7"})()

    limiter = RateLimiter(
        enabled=True, max_requests=1, window_seconds=60, store=CapturingStore()
    )
    app = FastAPI()

    @app.post("/api/v1/chat/query")
    async def limited(
        _: None = Depends(identify),
        __: None = Depends(limiter),
    ):
        return {"ok": True}

    with TestClient(app) as client:
        assert client.post("/api/v1/chat/query").status_code == 200
    assert captured == ["rl:principal-7:POST:/api/v1/chat/query:generation"]


def test_sliding_window_store_respects_injectable_clock():
    clock = [0.0]

    def fake_clock():
        return clock[0]

    store = SlidingWindowStore(clock=fake_clock)
    assert store.allow("k", limit=2, window_seconds=10) is True
    assert store.allow("k", limit=2, window_seconds=10) is True
    assert store.allow("k", limit=2, window_seconds=10) is False  # limit hit

    clock[0] = 11.0  # move outside the window -> resets
    assert store.allow("k", limit=2, window_seconds=10) is True
