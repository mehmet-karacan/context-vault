"""Aşama 9.4-9.5 tests: structured logging + request_id threading, CORS /
debug / stack-trace hardening, and the rate limiter.

These are unit tests: the structured-logger tests run the formatter / middleware
directly, and the CORS / debug / rate-limit tests build throwaway apps so no
real database, Redis, MinIO or gateway is touched.
"""

import io
import importlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Request
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.config import Settings
from src.infrastructure.observability import (
    ALLOWED_COUNTERS,
    ALLOWED_DURATIONS,
    MetricsCollector,
    ReadinessChecker,
    RequestContextMiddleware,
    StructuredJsonFormatter,
    StatsDMetricsSink,
    begin_shutdown,
    build_default_readiness_checks,
    canonical_traceparent,
    configure_metrics_export,
    continue_trace,
    current_traceparent,
    get_request_id,
    install_sqlalchemy_metrics,
    metrics,
    parse_traceparent,
    record_age_gauge,
    record_db_index_snapshot,
    record_maintenance_snapshot,
    record_operational_receipt_ages,
    record_outbox_snapshot,
    record_provider_usage,
    record_queue_snapshot,
    reset_shutdown,
    set_request_id,
    trace_operation,
)
from src.infrastructure.rate_limiter import RateLimiter, SlidingWindowStore
from src.main import create_app


@pytest.fixture(autouse=True)
def _clean_shutdown_state():
    reset_shutdown()
    metrics.configure_sink(None)
    yield
    metrics.configure_sink(None)
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
        unsampled = client.get("/trace", headers={"traceparent": parent[:-2] + "00"})
        invalid = client.get("/trace", headers={"traceparent": "not-a-trace"})
        duplicate = client.get(
            "/trace",
            headers=[
                ("traceparent", parent),
                ("traceparent", f"00-{'c' * 32}-{'d' * 16}-01"),
            ],
        )

    assert continued.headers["traceparent"].split("-")[1] == trace_id
    assert continued.headers["traceparent"].split("-")[2] != "b" * 16
    assert unsampled.headers["traceparent"].endswith("-00")
    assert invalid.headers["traceparent"].split("-")[1] != trace_id
    assert duplicate.headers["traceparent"].split("-")[1] not in {
        trace_id,
        "c" * 32,
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "00-" + "0" * 32 + "-" + "1" * 16 + "-01",
        "00-" + "1" * 32 + "-" + "0" * 16 + "-01",
        "ff-" + "1" * 32 + "-" + "2" * 16 + "-01",
        "00-" + "g" * 32 + "-" + "2" * 16 + "-01",
        "00-" + "A" * 32 + "-" + "2" * 16 + "-01",
        "00-" + "1" * 32 + "-" + "2" * 16 + "-01-extra",
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


def test_future_traceparent_is_accepted_and_canonicalized_to_base_fields():
    future = f"01-{'1' * 32}-{'2' * 16}-00-opaque.value/vendor_x"
    assert parse_traceparent(future) == ("1" * 32, "2" * 16, "00")
    assert canonical_traceparent(future) == f"00-{'1' * 32}-{'2' * 16}-00"


@pytest.mark.parametrize(("flags", "expected"), [("ff", "01"), ("fe", "00")])
def test_outgoing_traceparent_masks_reserved_flag_bits(flags, expected):
    incoming = f"00-{'1' * 32}-{'2' * 16}-{flags}"
    assert canonical_traceparent(incoming).endswith(f"-{expected}")
    with continue_trace(incoming):
        assert current_traceparent().endswith(f"-{expected}")


def test_invalid_ingress_parent_clears_ambient_trace_without_leaking_context():
    parent = f"00-{'1' * 32}-{'2' * 16}-01"
    with continue_trace(parent):
        assert current_traceparent() == parent
        with continue_trace("private-sentinel-invalid-parent"):
            assert current_traceparent() is None
            with trace_operation("ingestion.process"):
                replacement = current_traceparent()
                assert replacement is not None
                assert replacement.split("-")[1] != "1" * 32
        assert current_traceparent() == parent


@pytest.mark.parametrize(
    "value", [None, 7, {}, "private-sentinel-" * 200, "00-not-valid"]
)
def test_canonical_traceparent_never_forwards_unvalidated_durable_payload(value):
    assert canonical_traceparent(value) is None


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


def test_provider_usage_records_only_bounded_aggregate_micro_usd():
    before = metrics.snapshot()["counters"].get("provider.cost_micro_usd", 0)
    response = SimpleNamespace(
        response_cost=0,
        usage=SimpleNamespace(total_tokens=11),
        _hidden_params={"response_cost": "0.0012345", "prompt": "private"},
    )
    record_provider_usage(response)
    after = metrics.snapshot()
    assert after["counters"]["provider.cost_micro_usd"] == before + 1235
    assert "private" not in json.dumps(after)

    record_provider_usage(
        SimpleNamespace(
            usage=SimpleNamespace(total_tokens=True),
            _hidden_params={"response_cost": "NaN"},
        )
    )
    assert metrics.snapshot()["counters"]["provider.cost_micro_usd"] == before + 1235


def test_queue_snapshot_records_depth_and_oldest_age_without_payload():
    envelope = json.dumps(
        {
            "headers": {"cv_enqueued_at_epoch": 100},
            "body": "private-sentinel-payload",
        }
    ).encode()
    assert record_queue_snapshot(depth=3, oldest_message=envelope, now_epoch=145)
    snapshot = metrics.snapshot()
    assert snapshot["gauges"]["queue.depth"] == 3
    assert snapshot["gauges"]["queue.oldest_age"] == 45
    assert "private-sentinel-payload" not in json.dumps(snapshot)

    assert not record_queue_snapshot(depth=1, oldest_message=b"not-json", now_epoch=145)
    assert "queue.oldest_age" not in metrics.snapshot()["gauges"]
    assert not record_queue_snapshot(depth=1, oldest_message=None, now_epoch=145)
    future = json.dumps({"headers": {"cv_enqueued_at_epoch": 146}})
    assert not record_queue_snapshot(depth=1, oldest_message=future, now_epoch=145)
    assert not record_queue_snapshot(depth=-1, oldest_message=None, now_epoch=145)
    assert "queue.depth" not in metrics.snapshot()["gauges"]


def test_outbox_and_receipt_ages_are_bounded_content_free_gauges():
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    oldest = now - timedelta(seconds=75)
    assert record_outbox_snapshot(backlog=4, oldest_created_at=oldest, now=now)
    assert record_age_gauge(
        "backup.age_seconds", completed_at=now - timedelta(hours=2), now=now
    )
    assert record_age_gauge(
        "restore_drill.age_seconds", completed_at=now - timedelta(days=3), now=now
    )
    gauges = metrics.snapshot()["gauges"]
    assert gauges["outbox.backlog"] == 4
    assert gauges["outbox.oldest_age"] == 75
    assert gauges["backup.age_seconds"] == 7200
    assert gauges["restore_drill.age_seconds"] == 259200
    assert not record_outbox_snapshot(backlog=1, oldest_created_at=now, now=oldest)
    assert "outbox.oldest_age" not in metrics.snapshot()["gauges"]


def test_runtime_observer_projects_only_eligible_receipt_ages(tmp_path: Path) -> None:
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    backup = tmp_path / "backup.json"
    restore = tmp_path / "restore.json"
    backup.write_text(
        json.dumps(
            {
                "receipt_type": "backup-complete",
                "status": "PASS",
                "release_gate_eligible": True,
                "source_mutated": False,
                "credential_values_retained": False,
                "raw_object_names_retained_in_receipt": False,
                "completed_at_utc": "2026-09-06T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    restore.write_text(
        json.dumps(
            {
                "receipt_type": "fresh-target-restore-drill",
                "status": "PASS",
                "release_gate_eligible": True,
                "fresh_target_verified": True,
                "source_mutated": False,
                "credential_values_retained": False,
                "raw_object_names_retained": False,
                "restore_finished_at_utc": "2026-09-06T11:30:00Z",
            }
        ),
        encoding="utf-8",
    )

    assert record_operational_receipt_ages(
        backup_path=str(backup), restore_path=str(restore), now=now
    )
    gauges = metrics.snapshot()["gauges"]
    assert gauges["backup.age_seconds"] == 7200
    assert gauges["restore_drill.age_seconds"] == 1800

    backup_payload = json.loads(backup.read_text(encoding="utf-8"))
    backup_payload["release_gate_eligible"] = False
    backup.write_text(json.dumps(backup_payload), encoding="utf-8")
    assert not record_operational_receipt_ages(
        backup_path=str(backup), restore_path=str(restore), now=now
    )
    assert "backup.age_seconds" not in metrics.snapshot()["gauges"]


def test_sqlalchemy_metrics_record_queries_slow_count_and_current_pool_depth():
    engine = create_engine("sqlite://", poolclass=QueuePool)
    before = metrics.snapshot()
    install_sqlalchemy_metrics(engine, slow_query_seconds=0.000000001)
    install_sqlalchemy_metrics(engine, slow_query_seconds=0.000000001)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    after = metrics.snapshot()
    assert after["durations"]["db.query"]["count"] == (
        before["durations"].get("db.query", {}).get("count", 0) + 1
    )
    assert after["counters"]["db.slow_queries"] == (
        before["counters"].get("db.slow_queries", 0) + 1
    )
    assert after["gauges"]["db.pool.checked_out"] == 0
    engine.dispose()


def test_content_free_statsd_sink_and_duration_p95_are_consumable():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(1)
    collector = MetricsCollector()
    collector.configure_sink(
        StatsDMetricsSink("127.0.0.1", server.getsockname()[1], service="worker")
    )
    collector.incr("job.retries", 2)
    collector.set_gauge("queue.depth", 3)
    collector.clear_gauge("queue.depth")
    for duration in range(1, 21):
        collector.record_duration("job.stage", duration / 1000)
    wire = {server.recv(512).decode("ascii") for _ in range(23)}
    server.close()
    assert "context_vault.worker.job.retries:2|c" in wire
    gauge_packets = [packet for packet in wire if ".queue.depth." in packet]
    assert len(gauge_packets) == 2
    assert any(
        "context_vault.worker.queue.depth:3|g" in packet
        and "context_vault.worker.queue.depth.known:1|g" in packet
        and "context_vault.worker.queue.depth.observed_at_epoch:" in packet
        for packet in gauge_packets
    )
    assert any(
        "context_vault.worker.queue.depth.known:0|g" in packet
        and "context_vault.worker.queue.depth.observed_at_epoch:" in packet
        for packet in gauge_packets
    )
    snapshot = collector.snapshot()
    assert snapshot["durations"]["job.stage"]["p95_ms"] == 19
    assert "private" not in json.dumps(snapshot)


def test_metrics_export_config_is_side_effect_free_local_and_required_production():
    local = SimpleNamespace(
        APP_ENV="local",
        METRICS_EXPORT_MODE="disabled",
        METRICS_STATSD_HOST=None,
        METRICS_STATSD_PORT=8125,
    )
    configure_metrics_export(local, service="backend")
    with pytest.raises(ValueError, match="requires StatsD"):
        configure_metrics_export(
            SimpleNamespace(**{**vars(local), "APP_ENV": "production"}),
            service="backend",
        )
    configure_metrics_export(local, service="backend")


def test_backend_task_import_does_not_relabel_metrics_as_worker() -> None:
    environment = {
        **os.environ,
        "APP_ENV": "local",
        "AUTH_MODE": "disabled",
        "BIND_HOST": "127.0.0.1",
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
        "LITELLM_API_KEY": "test-key-not-used",
        "OBJECT_STORAGE_ENCRYPTION_KEY": (
            "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
        ),
        "METRICS_EXPORT_MODE": "statsd",
        "METRICS_STATSD_HOST": "127.0.0.1",
        "METRICS_STATSD_PORT": "8125",
    }
    code = """
from src.main import app
from src.infrastructure.observability import metrics
assert metrics._sink._prefix == 'context_vault.backend'
import src.workers.ingestion_tasks
assert metrics._sink._prefix == 'context_vault.backend'
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_worker_process_init_configures_process_local_metrics(monkeypatch) -> None:
    worker_module = importlib.import_module("src.workers.celery_app")
    configured = []
    contexts = []
    monkeypatch.setattr(
        worker_module,
        "configure_metrics_export",
        lambda configuration, *, service: configured.append((configuration, service)),
    )
    monkeypatch.setattr(
        worker_module,
        "set_observability_context",
        lambda **values: contexts.append(values),
    )

    worker_module._open_worker_process_metrics()

    assert configured == [(worker_module.settings, "worker")]
    assert contexts == [
        {"service": "worker", "environment": worker_module.settings.APP_ENV}
    ]


def test_production_worker_import_fails_closed_without_exporter() -> None:
    environment = {
        **os.environ,
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql://runtime:synthetic@db:5432/context_vault",
        "LITELLM_API_KEY": "synthetic-not-used",
        "METRICS_EXPORT_MODE": "disabled",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import src.workers.celery_app"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert "requires StatsD metrics export" in result.stderr
    assert "synthetic-not-used" not in result.stderr


def test_index_and_maintenance_snapshots_are_bounded_current_gauges():
    assert record_db_index_snapshot(scans_total=Decimal("15"), unused_count=2)
    assert record_maintenance_snapshot(stale_leases=3, orphan_objects=4)
    gauges = metrics.snapshot()["gauges"]
    assert gauges["db.index.scans_total"] == 15
    assert gauges["db.index.unused_count"] == 2
    assert gauges["lease.stale"] == 3
    assert gauges["orphan.objects"] == 4
    assert not record_db_index_snapshot(scans_total=-1, unused_count=2)
    assert not record_maintenance_snapshot(stale_leases=0.5, orphan_objects=0)


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
        "METRICS_EXPORT_MODE": (
            "statsd" if app_env in {"production", "staging"} else "disabled"
        ),
        "METRICS_STATSD_HOST": (
            "127.0.0.1" if app_env in {"production", "staging"} else None
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


def test_debug_error_still_hides_stack_details_from_http_clients(monkeypatch):
    monkeypatch.setattr("src.main.init_db", lambda: None)
    app = create_app(_secured_settings("development", API_DEBUG=True))
    app.add_api_route("/boom", _boom, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal Server Error"
    assert "top-secret-stack-frame" not in json.dumps(body)


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
