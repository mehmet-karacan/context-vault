"""Aşama 9.4-9.5 tests: structured logging + request_id threading, CORS /
debug / stack-trace hardening, and the rate limiter.

These are unit tests: the structured-logger tests run the formatter / middleware
directly, and the CORS / debug / rate-limit tests build throwaway apps so no
real database, Redis, MinIO or gateway is touched.
"""

import io
import json
import logging

from fastapi import Depends, FastAPI
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.config import Settings
from src.infrastructure.observability import (
    ReadinessChecker,
    RequestContextMiddleware,
    StructuredJsonFormatter,
    get_request_id,
    metrics,
    set_request_id,
)
from src.infrastructure.rate_limiter import RateLimiter, SlidingWindowStore
from src.main import create_app


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
    assert data["job_id"] == "job-9"
    assert data["parser"] == "docling"
    assert data["latency_ms"] == 42
    assert data["level"] == "INFO"
    assert data["message"] == "job stage done"


def test_request_id_contextvar_threads_through_middleware():
    async def route(request):
        return JSONResponse({"rid": get_request_id()})

    app = Starlette(routes=[Route("/rid", route)])
    app.add_middleware(RequestContextMiddleware)

    with TestClient(app) as client:
        body = client.get("/rid").json()
        assert body["rid"]
        # A second request gets a different id.
        body2 = client.get("/rid").json()
        assert body2["rid"]
        assert body["rid"] != body2["rid"]


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


# --- Readiness (health vs readiness split) ---------------------------------


def test_readiness_ok_when_all_dependencies_up():
    checker = ReadinessChecker(
        {"db": lambda: True, "redis": lambda: True, "minio": lambda: True, "gateway": lambda: True}
    )
    result = checker.run()
    assert result["status"] == "ok"
    assert result["dependencies"] == {
        "db": "ok",
        "redis": "ok",
        "minio": "ok",
        "gateway": "ok",
    }


def test_readiness_degraded_not_crash_when_dependency_down():
    checker = ReadinessChecker(
        {"db": lambda: True, "redis": lambda: False, "minio": lambda: True, "gateway": lambda: True}
    )
    result = checker.run()  # must not raise
    assert result["status"] == "degraded"
    assert result["dependencies"]["redis"] == "down"
    assert result["dependencies"]["db"] == "ok"


def test_readiness_treats_raising_checker_as_down():
    def boom():
        raise RuntimeError("minio unreachable")

    checker = ReadinessChecker({"minio": boom, "db": lambda: True})
    result = checker.run()
    assert result["status"] == "degraded"
    assert result["dependencies"]["minio"] == "down"
    assert result["dependencies"]["db"] == "ok"


# --- CORS / debug / stack-trace hardening ----------------------------------


def _cors_origins(app) -> list:
    for m in app.user_middleware:
        if getattr(m, "cls", None).__name__ == "CORSMiddleware":
            return list(m.kwargs["allow_origins"])
    return []


def test_cors_never_wildcard_in_production():
    prod_app = create_app(Settings(APP_ENV="production"))
    origins = _cors_origins(prod_app)
    assert "*" not in origins
    assert origins == []


def test_cors_defaults_to_dev_origins_in_development():
    dev_app = create_app(Settings(APP_ENV="development"))
    origins = _cors_origins(dev_app)
    assert "http://localhost:3000" in origins
    assert "*" not in origins


def test_cors_respects_explicit_allowlist():
    app = create_app(
        Settings(
            APP_ENV="production",
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
    app = create_app(Settings(APP_ENV="development", API_DEBUG=False))
    app.add_api_route("/boom", _boom, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal Server Error"
    assert "top-secret-stack-frame" not in json.dumps(body)


def test_debug_error_includes_stack_details_when_api_debug_on(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)
    app = create_app(Settings(APP_ENV="development", API_DEBUG=True))
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
