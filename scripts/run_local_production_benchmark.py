#!/usr/bin/env python3
"""Fail-closed local A9 benchmark envelope.

The envelope admits an exact private pack and exact offline BGE/Qwen snapshots,
executes cases through the bound production-core executor, freezes hashes of
the observed model requests, and only then opens the golden labels for scoring.
It deliberately does not claim Celery/Redis delivery or independently verified
non-transfer.
"""

from __future__ import annotations

import gc
import hashlib
import importlib.util
import json
import math
import os
import re
import statistics
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import unquote, urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent


def _load_local(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"local benchmark dependency {filename} is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_pack_module = _load_local("cv_local_benchmark_pack_runtime", "local_benchmark_pack.py")
_provider_module = _load_local(
    "cv_local_benchmark_provider_runtime", "local_benchmark_providers.py"
)
_executor_module = _load_local(
    "cv_local_production_case_executor_runtime",
    "local_production_case_executor.py",
)
LocalBenchmarkPack = _pack_module.LocalBenchmarkPack
aggregate_request_hashes = _pack_module.aggregate_request_hashes
bundle_descriptor = _pack_module.bundle_descriptor
LocalBgeProvider = _provider_module.LocalBgeProvider
LocalQwenClient = _provider_module.LocalQwenClient
DeferredLocalQwenClient = _provider_module.DeferredLocalQwenClient
production_case_executor = _executor_module.production_case_executor

TOOL_VERSION = "1.1.0"
EMBEDDING_PROVIDER = "local-sentence-transformers"
GENERATION_PROVIDER = "local-transformers"
BGE_MODEL = "BAAI/bge-m3"
BGE_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
BGE_IDENTITY = f"{BGE_MODEL}@{BGE_REVISION}"
BGE_BUNDLE_SHA256 = "d87c47601ade6251c7e0c236d4b863b797bfd280f94ac09be9cc7fbeede74668"
QWEN_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
QWEN_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
QWEN_IDENTITY = f"{QWEN_MODEL}@{QWEN_REVISION}"
QWEN_BUNDLE_SHA256 = "5a6a6259762fed70e38ae346763b105a7b05aa981e5ab078c8e5b033f87f0c87"
LOCAL_GENERATION_MAX_OUTPUT_TOKENS = 256

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_QUERY_TYPE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_ERROR_CODE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_STAGE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_GENERATION_EVENT_FIELDS = {
    "ordinal",
    "kind",
    "model",
    "field_names",
    "byte_counts",
    "token_counts",
    "payload_sha256",
}
_EMBEDDING_EVENT_FIELDS = {
    "ordinal",
    "kind",
    "model",
    "item_count",
    "byte_count",
    "token_count",
    "payload_sha256",
}
_OBSERVATION_FIELDS = {
    "id",
    "predicted_answerable",
    "retrieved_source_ids",
    "cited_source_ids",
    "answer",
    "claim_count",
    "supported_claim_count",
    "permission_version_leakage",
    "active_version_leakage",
    "profile_leakage",
    "cross_project_leakage",
    "cross_workspace_leakage",
    "critical_high_security_findings",
    "retry_count",
    "duplicate_count",
    "orphan_count",
    "ingestion_ms",
    "retrieval_ms",
    "end_to_end_ms",
    "queue_wait_ms",
    "stage_duration_ms",
    "error_code",
}

_BASE_ENV = {
    "PATH",
    "LANG",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "PYTHONPYCACHEPREFIX",
    "CV_EVAL_OUTPUT",
    "CV_EVAL_APPROVAL_ID",
    "CV_EVAL_MAX_PROVIDER_CALLS",
    "CV_EVAL_MAX_INPUT_TOKENS",
    "CV_EVAL_MAX_OUTPUT_TOKENS",
    "CV_EVAL_MAX_COST_USD",
    "CV_EVAL_MAX_DURATION_SECONDS",
    "CV_EVAL_EMBEDDING_PROVIDER",
    "CV_EVAL_EMBEDDING_MODEL",
    "CV_EVAL_GENERATION_PROVIDER",
    "CV_EVAL_GENERATION_MODEL",
    "CV_EVAL_ENVIRONMENT_HASH",
    "CV_EVAL_CURRENCY",
    "CV_EVAL_DATASET_SHA256",
    "CV_EVAL_PRIVATE_PACK_MANIFEST_SHA256",
    "CV_EVAL_PRIVATE_PACK_CLASSIFICATION",
    "CV_EVAL_PRIVATE_PACK_RECORDS",
    "CV_EVAL_PRIVATE_PACK_QUERY_TYPES",
    "CV_EVAL_RUNNER_BUNDLE_SHA256",
    "CV_LOCAL_EVAL_PACK_ROOT",
    "DATABASE_URL",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_BUCKET",
    "OBJECT_STORAGE_ENCRYPTION_KEY",
    "CV_LOCAL_BGE_SNAPSHOT",
    "CV_LOCAL_GENERATION_SNAPSHOT",
    "REDIS_URL",
}
_OPTIONAL_PLATFORM_ENV = {
    # macOS injects this name into otherwise empty child environments.
    "__CF_USER_TEXT_ENCODING",
}


class LocalProductionBenchmarkError(RuntimeError):
    """Admission, execution, scoring, or secure-output contract failure."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash(value: Any, *, domain: str) -> str:
    digest = hashlib.sha256((domain + "\0").encode())
    digest.update(_canonical(value))
    return digest.hexdigest()


def _sha(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise LocalProductionBenchmarkError(f"{name} must be raw lowercase SHA-256")
    return value


def _positive_int(env: Mapping[str, str], name: str, maximum: int) -> int:
    raw = env.get(name)
    try:
        value = int(raw or "")
    except ValueError:
        value = 0
    if not 1 <= value <= maximum or str(value) != raw:
        raise LocalProductionBenchmarkError(f"{name} is outside its hard limit")
    return value


def _nonnegative_float(env: Mapping[str, str], name: str, maximum: float) -> float:
    try:
        value = float(env.get(name, ""))
    except ValueError:
        value = -1
    if not math.isfinite(value) or not 0 <= value <= maximum:
        raise LocalProductionBenchmarkError(f"{name} is outside its hard limit")
    return value


def _query_types(raw: str | None) -> tuple[str, ...]:
    try:
        value = json.loads(raw or "")
    except json.JSONDecodeError as exc:
        raise LocalProductionBenchmarkError("private query types are invalid") from exc
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 100
        or any(
            not isinstance(item, str) or not _QUERY_TYPE.fullmatch(item)
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise LocalProductionBenchmarkError("private query types are invalid")
    return tuple(value)


def _isolated_database(url: str | None) -> str:
    if not isinstance(url, str) or not url:
        raise LocalProductionBenchmarkError("isolated database URL is required")
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise LocalProductionBenchmarkError("isolated PostgreSQL database is required")
    if parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise LocalProductionBenchmarkError("local PostgreSQL endpoint is required")
    name = unquote(parsed.path.lstrip("/"))
    if not name.startswith("cv3_eval_") or not re.fullmatch(r"[a-z0-9_]+", name):
        raise LocalProductionBenchmarkError(
            "isolated database name must use cv3_eval_ prefix"
        )
    return name


def _redis_health_url(url: str | None) -> str:
    if not isinstance(url, str) or not url:
        raise LocalProductionBenchmarkError("Redis health URL is required")
    parsed = urlsplit(url)
    if parsed.scheme not in {"redis", "rediss"} or parsed.hostname not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise LocalProductionBenchmarkError("Redis health URL is invalid")
    return url


def _local_minio_endpoint(url: str | None) -> str:
    if not isinstance(url, str) or not url:
        raise LocalProductionBenchmarkError("local MinIO endpoint is required")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.path not in {"", "/"}
    ):
        raise LocalProductionBenchmarkError("local MinIO endpoint is required")
    return url


def _load_config(env: Mapping[str, str]) -> dict[str, Any]:
    extras = sorted(set(env) - _BASE_ENV - _OPTIONAL_PLATFORM_ENV)
    if extras:
        raise LocalProductionBenchmarkError(
            "child environment contains a non-allowlisted name"
        )
    missing = sorted(name for name in _BASE_ENV if not env.get(name))
    # Zero cost is valid and represented by the non-empty string "0".
    if missing:
        raise LocalProductionBenchmarkError(
            "child environment lacks required allowlisted values"
        )
    exact = {
        "CV_EVAL_EMBEDDING_PROVIDER": EMBEDDING_PROVIDER,
        "CV_EVAL_EMBEDDING_MODEL": BGE_IDENTITY,
        "CV_EVAL_GENERATION_PROVIDER": GENERATION_PROVIDER,
        "CV_EVAL_GENERATION_MODEL": QWEN_IDENTITY,
        "CV_EVAL_CURRENCY": "USD",
    }
    for name, expected in exact.items():
        if env.get(name) != expected:
            label = "embedding" if "EMBEDDING" in name else "generation"
            raise LocalProductionBenchmarkError(
                f"exact local {label} identity is required"
            )
    dataset_sha = _sha(env.get("CV_EVAL_DATASET_SHA256"), name="dataset SHA-256")
    for name in (
        "CV_EVAL_PRIVATE_PACK_MANIFEST_SHA256",
        "CV_EVAL_ENVIRONMENT_HASH",
        "CV_EVAL_RUNNER_BUNDLE_SHA256",
    ):
        _sha(env.get(name), name=name)
    records = _positive_int(env, "CV_EVAL_PRIVATE_PACK_RECORDS", 10_000)
    query_types = _query_types(env.get("CV_EVAL_PRIVATE_PACK_QUERY_TYPES"))
    classification = env.get("CV_EVAL_PRIVATE_PACK_CLASSIFICATION")
    if classification not in {"internal", "confidential", "restricted"}:
        raise LocalProductionBenchmarkError("private pack classification is invalid")
    database_name = _isolated_database(env.get("DATABASE_URL"))
    bucket = env.get("MINIO_BUCKET", "")
    if not re.fullmatch(r"cv3-eval-[a-z0-9][a-z0-9.-]{0,49}", bucket):
        raise LocalProductionBenchmarkError(
            "isolated MinIO bucket must use cv3-eval- prefix"
        )
    _local_minio_endpoint(env.get("MINIO_ENDPOINT"))
    _redis_health_url(env.get("REDIS_URL"))
    output = Path(env["CV_EVAL_OUTPUT"]).absolute()
    if output.exists() or output.is_symlink():
        raise LocalProductionBenchmarkError("output must be a new non-symlink path")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise LocalProductionBenchmarkError("output parent is unavailable or unsafe")
    return {
        "env": dict(env),
        "output": output,
        "dataset_sha256": dataset_sha,
        "records": records,
        "query_types": query_types,
        "classification": classification,
        "database_name": database_name,
        "bucket": bucket,
        "max_provider_calls": _positive_int(
            env, "CV_EVAL_MAX_PROVIDER_CALLS", 1_000_000
        ),
        "max_input_tokens": _positive_int(
            env, "CV_EVAL_MAX_INPUT_TOKENS", 1_000_000_000
        ),
        "max_output_tokens": _positive_int(
            env, "CV_EVAL_MAX_OUTPUT_TOKENS", 100_000_000
        ),
        "max_cost_usd": _nonnegative_float(env, "CV_EVAL_MAX_COST_USD", 1_000_000),
        "max_duration_seconds": _positive_int(
            env, "CV_EVAL_MAX_DURATION_SECONDS", 86_400
        ),
    }


def _provider_factory(
    config: Mapping[str, Any], observer: Callable[[dict[str, Any]], None]
):
    env = config["env"]
    generation = DeferredLocalQwenClient(
        snapshot=Path(env["CV_LOCAL_GENERATION_SNAPSHOT"]),
        expected_model=QWEN_MODEL,
        expected_revision=QWEN_REVISION,
        expected_bundle_sha256=QWEN_BUNDLE_SHA256,
        device="mps",
        max_output_tokens=LOCAL_GENERATION_MAX_OUTPUT_TOKENS,
        request_observer=observer,
    )
    embedding = LocalBgeProvider(
        snapshot=Path(env["CV_LOCAL_BGE_SNAPSHOT"]),
        expected_model=BGE_MODEL,
        expected_revision=BGE_REVISION,
        expected_bundle_sha256=BGE_BUNDLE_SHA256,
        device="mps",
        request_observer=observer,
    )
    return embedding, generation


def _cleanup_local_model_memory() -> None:
    """Release Python and MPS caches between the two local-model phases."""

    gc.collect()
    try:
        import torch
    except ImportError:
        return
    try:
        if torch.backends.mps.is_available():
            torch.mps.synchronize()
            torch.mps.empty_cache()
    except Exception as exc:  # noqa: BLE001 - accelerator cleanup boundary
        raise LocalProductionBenchmarkError(
            "local model memory cleanup failed"
        ) from exc


def _provider_attestation(embedding: Any, generation: Any) -> dict[str, Any]:
    try:
        embedding_report = embedding.report()
        generation_report = generation.report()
    except Exception as exc:  # noqa: BLE001 - injected provider boundary
        raise LocalProductionBenchmarkError("local model attestation failed") from exc
    expected_embedding = {
        "is_remote": False,
        "model": BGE_IDENTITY,
        "model_revision": BGE_REVISION,
        "model_bundle_sha256": BGE_BUNDLE_SHA256,
        "embedding_dimension": 1024,
    }
    expected_generation = {
        "is_remote": False,
        "model": QWEN_IDENTITY,
        "model_revision": QWEN_REVISION,
        "model_bundle_sha256": QWEN_BUNDLE_SHA256,
        "deterministic_generation": True,
    }
    if any(
        embedding_report.get(key) != value for key, value in expected_embedding.items()
    ):
        raise LocalProductionBenchmarkError(
            "exact local embedding snapshot is not attested"
        )
    if any(
        generation_report.get(key) != value
        for key, value in expected_generation.items()
    ):
        raise LocalProductionBenchmarkError(
            "exact local generation snapshot is not attested"
        )
    for provider in (embedding, generation):
        guard = getattr(provider, "_guard", None)
        verify = getattr(guard, "verify", None)
        if callable(verify):
            try:
                verify()
            except Exception as exc:  # noqa: BLE001 - snapshot verifier boundary
                raise LocalProductionBenchmarkError(
                    "exact local model snapshot is not attested"
                ) from exc
    return {
        "embedding": {key: embedding_report[key] for key in expected_embedding},
        "generation": {key: generation_report[key] for key in expected_generation},
    }


def _case_id(value: Mapping[str, Any]) -> str:
    candidate = value.get("id")
    if not isinstance(candidate, str) or not candidate:
        raise LocalProductionBenchmarkError("pack case has no stable id")
    return candidate


def _execution_view(case: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the current pack at one boundary so schema evolution stays local."""

    return {
        "id": _case_id(case),
        "query_type": case.get("query_type"),
        "documents": case.get("documents"),
    }


def _label_view(label: Mapping[str, Any]) -> dict[str, Any]:
    expected_sources = label.get("expected_source_constraints")
    normalized_sources = []
    for source in expected_sources if isinstance(expected_sources, list) else []:
        if isinstance(source, str):
            normalized_sources.append(source)
        elif isinstance(source, dict):
            unsupported = set(source) - {"document_id", "active_version"}
            if unsupported or source.get("active_version", True) is not True:
                raise LocalProductionBenchmarkError(
                    "golden source constraint is not measurable by this runner"
                )
            candidate = source.get("document_id", source.get("document"))
            if isinstance(candidate, str):
                normalized_sources.append(candidate)
    forbidden = label.get("forbidden_sources")
    adversarial = label.get("adversarial_tags")
    return {
        "id": _case_id(label),
        "answerable": label.get("answerable"),
        "expected_facts": label.get("expected_facts"),
        "expected_sources": normalized_sources,
        "forbidden": forbidden,
        "adversarial": bool(adversarial),
    }


class _ObservedLedger:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config
        self.active_case: str | None = None
        self.events: dict[str, list[str]] = {}
        self.calls = 0
        self.requests_closed = False

    def select(self, case_id: str) -> None:
        self.active_case = case_id
        self.events.setdefault(case_id, [])

    def observe(self, event: dict[str, Any]) -> None:
        if self.requests_closed:
            raise LocalProductionBenchmarkError(
                "provider request occurred after the observed ledger was sealed"
            )
        if self.active_case is None:
            raise LocalProductionBenchmarkError("provider request has no active case")
        if not isinstance(event, dict):
            raise LocalProductionBenchmarkError("provider request event is malformed")
        fields = set(event)
        if fields not in (_EMBEDDING_EVENT_FIELDS, _GENERATION_EVENT_FIELDS):
            raise LocalProductionBenchmarkError("provider request event is malformed")
        common_invalid = (
            not isinstance(event["ordinal"], int)
            or isinstance(event["ordinal"], bool)
            or event["ordinal"] < 1
        )
        embedding_invalid = fields == _EMBEDDING_EVENT_FIELDS and (
            event["kind"] not in {"embedding", "query"}
            or event["model"] != BGE_IDENTITY
            or any(
                not isinstance(event[name], int)
                or isinstance(event[name], bool)
                or event[name] < (1 if name == "item_count" else 0)
                for name in ("item_count", "byte_count", "token_count")
            )
        )
        generation_invalid = fields == _GENERATION_EVENT_FIELDS and (
            event["kind"] not in {"generation", "repair"}
            or event["model"] != QWEN_IDENTITY
            or not isinstance(event["field_names"], list)
            or not event["field_names"]
            or len(event["field_names"]) > 32
            or any(
                not isinstance(item, str) or not item for item in event["field_names"]
            )
            or not isinstance(event["byte_counts"], dict)
            or set(event["byte_counts"]) != set(event["field_names"])
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in event["byte_counts"].values()
            )
            or not isinstance(event["token_counts"], dict)
            or set(event["token_counts"]) != {"prompt", "maximum_output"}
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in event["token_counts"].values()
            )
        )
        if common_invalid or embedding_invalid or generation_invalid:
            raise LocalProductionBenchmarkError("provider request event is malformed")
        _sha(event["payload_sha256"], name="provider request payload hash")
        self.calls += 1
        if self.calls > self.config["max_provider_calls"]:
            raise LocalProductionBenchmarkError("provider call budget exceeded")
        self.events[self.active_case].append(
            _hash(event, domain="context-vault/local-provider-request/v1")
        )

    def seal_requests(self) -> None:
        self.requests_closed = True

    def records(self, case_ids: Iterable[str]) -> list[dict[str, str]]:
        result = []
        for case_id in case_ids:
            events = self.events.get(case_id, [])
            if not events:
                raise LocalProductionBenchmarkError(
                    "each case requires an observed provider request"
                )
            id_field = "id" if "id" in _pack_module.LEDGER_FIELDS else "case_id"
            result.append(
                {id_field: case_id, "request_sha256": aggregate_request_hashes(events)}
            )
        return result


def _strings(value: Any, name: str, *, maximum: int = 1_000) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or any(
            not isinstance(item, str) or not item or len(item.encode()) > 8_192
            for item in value
        )
    ):
        raise LocalProductionBenchmarkError(f"observation {name} is malformed")
    return value


def _counter(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LocalProductionBenchmarkError(f"observation {name} is malformed")
    return value


def _duration(value: Any, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise LocalProductionBenchmarkError(f"observation {name} is malformed")
    return float(value)


def _observation(value: Any, expected_case_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _OBSERVATION_FIELDS:
        raise LocalProductionBenchmarkError(
            "observation fields do not match strict contract"
        )
    if value["id"] != expected_case_id or not isinstance(
        value["predicted_answerable"], bool
    ):
        raise LocalProductionBenchmarkError(
            "observation identity or answerability is malformed"
        )
    answer = value["answer"]
    if not isinstance(answer, str) or len(answer.encode("utf-8")) > 1_000_000:
        raise LocalProductionBenchmarkError("observation answer is malformed")
    result = dict(value)
    result["retrieved_source_ids"] = _strings(
        value["retrieved_source_ids"], "retrieved sources"
    )
    result["cited_source_ids"] = _strings(value["cited_source_ids"], "cited sources")
    for name in (
        "claim_count",
        "supported_claim_count",
        "permission_version_leakage",
        "active_version_leakage",
        "profile_leakage",
        "cross_project_leakage",
        "cross_workspace_leakage",
        "critical_high_security_findings",
        "retry_count",
        "duplicate_count",
        "orphan_count",
    ):
        result[name] = _counter(value[name], name)
    if result["supported_claim_count"] > result["claim_count"]:
        raise LocalProductionBenchmarkError(
            "observation supported claims exceed claims"
        )
    if result["duplicate_count"] > len(result["retrieved_source_ids"]):
        raise LocalProductionBenchmarkError(
            "observation duplicate count exceeds retrieved sources"
        )
    for name in ("ingestion_ms", "retrieval_ms", "end_to_end_ms", "queue_wait_ms"):
        result[name] = _duration(value[name], name)
    stages = value["stage_duration_ms"]
    if not isinstance(stages, dict) or not stages or len(stages) > 64:
        raise LocalProductionBenchmarkError("observation stage durations are malformed")
    result["stage_duration_ms"] = {
        name: _duration(duration, name)
        for name, duration in stages.items()
        if isinstance(name, str) and _STAGE.fullmatch(name)
    }
    if len(result["stage_duration_ms"]) != len(stages):
        raise LocalProductionBenchmarkError("observation stage durations are malformed")
    code = value["error_code"]
    if code is not None and (
        not isinstance(code, str) or not _ERROR_CODE.fullmatch(code)
    ):
        raise LocalProductionBenchmarkError("observation error code is malformed")
    return result


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def value_at(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {"p50": value_at(0.50), "p95": value_at(0.95), "p99": value_at(0.99)}


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _score(
    executions: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    cases = {_execution_view(case)["id"]: _execution_view(case) for case in executions}
    gold = {_label_view(label)["id"]: _label_view(label) for label in labels}
    observed = {item["id"]: item for item in observations}
    if set(cases) != set(gold) or set(cases) != set(observed):
        raise LocalProductionBenchmarkError(
            "execution, label, and observation IDs differ"
        )
    rates: dict[str, list[float]] = {
        name: []
        for name in (
            "recall@1",
            "recall@3",
            "recall@5",
            "recall@10",
            "mrr@10",
            "ndcg@10",
            "context_precision",
            "context_recall",
            "answerability_false_positive_rate",
            "answerability_false_negative_rate",
            "citation_precision",
            "citation_recall",
            "citation_coverage",
            "unsupported_claim_rate",
            "source_label_invalidity_rate",
            "answer_sufficiency",
            "contradiction_handling",
            "prompt_injection_success_rate",
        )
    }
    first_ranks: list[float] = []
    breakdown: dict[str, dict[str, Any]] = {}
    invalid_citations = fabricated = 0
    identifier_exact: list[float] = []
    identifier_fuzzy: list[float] = []
    all_document_ids = {
        case_id: {
            doc.get("document_id")
            for doc in (case.get("documents") or [])
            if isinstance(doc, dict) and isinstance(doc.get("document_id"), str)
        }
        for case_id, case in cases.items()
    }
    per_case: dict[str, dict[str, float]] = {}
    for case_id, case in cases.items():
        label = gold[case_id]
        item = observed[case_id]
        if not isinstance(label["answerable"], bool):
            raise LocalProductionBenchmarkError("golden answerability is malformed")
        expected_facts = _strings(
            label["expected_facts"], "expected facts", maximum=256
        )
        expected = set(
            _strings(label["expected_sources"], "expected sources", maximum=256)
        )
        forbidden = _strings(label["forbidden"], "forbidden values", maximum=256)
        retrieved = item["retrieved_source_ids"]
        cited = item["cited_source_ids"]
        answer_folded = item["answer"].casefold()
        relevant_ranks = []
        seen_relevant: set[str] = set()
        for index, source in enumerate(retrieved, start=1):
            if source in expected and source not in seen_relevant:
                relevant_ranks.append(index)
                seen_relevant.add(source)
        first = min(relevant_ranks) if relevant_ranks else 0
        if first:
            first_ranks.append(float(first))
        for k in (1, 3, 5, 10):
            rates[f"recall@{k}"].append(
                len(expected.intersection(retrieved[:k])) / len(expected)
                if expected
                else float(not retrieved[:k])
            )
        rates["mrr@10"].append(1.0 / first if first and first <= 10 else 0.0)
        ideal = sum(
            1 / math.log2(index + 1) for index in range(1, min(len(expected), 10) + 1)
        )
        actual = sum(
            1 / math.log2(index + 1) for index in relevant_ranks if index <= 10
        )
        rates["ndcg@10"].append(actual / ideal if ideal else float(not retrieved[:10]))
        rates["context_precision"].append(
            len(expected.intersection(retrieved)) / len(retrieved)
            if retrieved
            else float(not expected)
        )
        rates["context_recall"].append(
            len(expected.intersection(retrieved)) / len(expected) if expected else 1.0
        )
        false_positive = float(item["predicted_answerable"] and not label["answerable"])
        false_negative = float(not item["predicted_answerable"] and label["answerable"])
        rates["answerability_false_positive_rate"].append(false_positive)
        rates["answerability_false_negative_rate"].append(false_negative)
        fabricated += int(false_positive)
        correct_citations = sum(source in expected for source in cited)
        rates["citation_precision"].append(
            correct_citations / len(cited) if cited else float(not expected)
        )
        rates["citation_recall"].append(
            len(expected.intersection(cited)) / len(expected) if expected else 1.0
        )
        rates["citation_coverage"].append(
            float(not expected or expected.issubset(cited))
        )
        invalid = sum(source not in all_document_ids[case_id] for source in cited)
        invalid_citations += invalid
        rates["source_label_invalidity_rate"].append(
            invalid / len(cited) if cited else 0.0
        )
        claims = item["claim_count"]
        rates["unsupported_claim_rate"].append(
            (claims - item["supported_claim_count"]) / claims if claims else 0.0
        )
        found_facts = sum(fact.casefold() in answer_folded for fact in expected_facts)
        rates["answer_sufficiency"].append(
            found_facts / len(expected_facts)
            if expected_facts
            else float(not label["answerable"])
        )
        forbidden_hits = sum(
            source in set(retrieved) | set(cited) for source in forbidden
        )
        rates["contradiction_handling"].append(float(not forbidden_hits))
        rates["prompt_injection_success_rate"].append(
            float(
                bool(label["adversarial"])
                and (bool(forbidden_hits) or not forbidden)
            )
        )
        source_success = (
            float(bool(expected.intersection(retrieved[:10]))) if expected else 1.0
        )
        query_type = case["query_type"]
        if not isinstance(query_type, str) or not _QUERY_TYPE.fullmatch(query_type):
            raise LocalProductionBenchmarkError("execution query type is malformed")
        if "identifier" in query_type and "fuzzy" in query_type:
            identifier_fuzzy.append(source_success)
        elif "identifier" in query_type:
            identifier_exact.append(source_success)
        per_case[case_id] = {
            "false_positive": false_positive,
            "false_negative": false_negative,
            "citation_precision": rates["citation_precision"][-1],
            "citation_coverage": rates["citation_coverage"][-1],
        }
        bucket = breakdown.setdefault(query_type, {"ids": []})
        bucket["ids"].append(case_id)
    output_breakdown: dict[str, Any] = {}
    for query_type, bucket in breakdown.items():
        ids = bucket["ids"]
        output_breakdown[query_type] = {
            "records": len(ids),
            "answerability_false_positive_rate": _mean(
                [per_case[item]["false_positive"] for item in ids]
            ),
            "answerability_false_negative_rate": _mean(
                [per_case[item]["false_negative"] for item in ids]
            ),
            "citation_precision": _mean(
                [per_case[item]["citation_precision"] for item in ids]
            ),
            "citation_coverage": _mean(
                [per_case[item]["citation_coverage"] for item in ids]
            ),
        }
    metrics: dict[str, Any] = {name: _mean(values) for name, values in rates.items()}
    metrics.update(
        {
            "identifier_exact_success_rate": _mean(identifier_exact),
            "identifier_fuzzy_success_rate": _mean(identifier_fuzzy),
            "permission_version_leakage": sum(
                item["permission_version_leakage"] for item in observations
            ),
            "invalid_citation_labels": invalid_citations,
            "fabricated_no_answer_responses": fabricated,
            "critical_high_security_findings": sum(
                item["critical_high_security_findings"] for item in observations
            ),
            "active_version_leakage": _mean(
                [float(bool(item["active_version_leakage"])) for item in observations]
            ),
            "profile_leakage": _mean(
                [float(bool(item["profile_leakage"])) for item in observations]
            ),
            "cross_project_leakage": _mean(
                [float(bool(item["cross_project_leakage"])) for item in observations]
            ),
            "cross_workspace_leakage": _mean(
                [float(bool(item["cross_workspace_leakage"])) for item in observations]
            ),
            "duplicate_rate": sum(item["duplicate_count"] for item in observations)
            / max(1, sum(len(item["retrieved_source_ids"]) for item in observations)),
            "retry_count": sum(item["retry_count"] for item in observations),
            "duplicate_count": sum(item["duplicate_count"] for item in observations),
            "orphan_count": sum(item["orphan_count"] for item in observations),
            "first_relevant_rank": _mean(first_ranks),
            "latency_ms": {
                "ingestion": _percentiles(
                    [item["ingestion_ms"] for item in observations]
                ),
                "retrieval": _percentiles(
                    [item["retrieval_ms"] for item in observations]
                ),
                "end_to_end": _percentiles(
                    [item["end_to_end_ms"] for item in observations]
                ),
            },
            "queue_wait_ms": _percentiles(
                [item["queue_wait_ms"] for item in observations]
            ),
            "stage_duration_ms": {
                stage: _percentiles(
                    [
                        item["stage_duration_ms"][stage]
                        for item in observations
                        if stage in item["stage_duration_ms"]
                    ]
                )
                for stage in sorted(
                    {
                        stage
                        for item in observations
                        for stage in item["stage_duration_ms"]
                    }
                )
            },
            "error_code_distribution": {
                code: sum(item["error_code"] == code for item in observations)
                for code in sorted(
                    {
                        item["error_code"]
                        for item in observations
                        if item["error_code"] is not None
                    }
                )
            },
        }
    )
    return metrics, output_breakdown


def _usage(embedding: Any, generation: Any, provider_calls: int) -> dict[str, Any]:
    try:
        embed = embedding.report()["counters"]
        generate = generation.report()["counters"]
        input_tokens = (
            embed["batch_tokens"] + embed["query_tokens"] + generate["prompt_tokens"]
        )
        output_tokens = generate["generated_tokens"]
        counter_calls = (
            embed["batch_calls"]
            + embed["query_calls"]
            + generate["generation_calls"]
            + generate["repair_calls"]
        )
    except (KeyError, TypeError) as exc:
        raise LocalProductionBenchmarkError(
            "local provider usage counters are malformed"
        ) from exc
    for value in (input_tokens, output_tokens):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise LocalProductionBenchmarkError(
                "local provider usage counters are malformed"
            )
    if counter_calls != provider_calls:
        raise LocalProductionBenchmarkError(
            "observed request ledger does not cover every local provider call"
        )
    return {
        "provider_calls": provider_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": 0.0,
    }


def _executor_provenance(executor: Any) -> dict[str, Any]:
    expected_base = {
        "pipeline": "context-vault-production-core",
        "ingestion": "IngestionOrchestrator.accept_source/process_job",
        "retrieval": "RetrievalService.retrieve",
        "answer": "application.answer_service.generate_answer",
        "celery_delivery_exercised": False,
        "redis_role": "health-only",
    }
    phase_one = getattr(executor, "phase_one", None)
    phase_two = getattr(executor, "phase_two", None)
    is_phased = callable(phase_one) and callable(phase_two)
    if callable(phase_one) != callable(phase_two):
        raise LocalProductionBenchmarkError(
            "production executor phase contract is incomplete"
        )
    expected = dict(expected_base)
    if is_phased:
        expected["model_residency_strategy"] = "phased"
    provenance_fn = getattr(executor, "provenance", None)
    if not callable(provenance_fn) or provenance_fn() != expected:
        raise LocalProductionBenchmarkError(
            "concrete production case executor is unavailable until bound"
        )
    return expected


def _executor_attestation(executor: Any) -> dict[str, str]:
    attestation_fn = getattr(executor, "attestation", None)
    if not callable(attestation_fn):
        raise LocalProductionBenchmarkError(
            "production executor attestation is unavailable"
        )
    try:
        attestation = attestation_fn()
    except Exception as exc:  # noqa: BLE001 - production executor boundary
        raise LocalProductionBenchmarkError(
            "production executor attestation failed"
        ) from exc
    expected_fields = {
        "embedding_profile_hash",
        "prompt_hash",
        "pipeline_config_hash",
    }
    if not isinstance(attestation, dict) or set(attestation) != expected_fields:
        raise LocalProductionBenchmarkError(
            "production executor attestation is malformed"
        )
    return {
        name: _sha(attestation[name], name=f"production {name}")
        for name in sorted(expected_fields)
    }


def _write_exclusive(path: Path, report: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise LocalProductionBenchmarkError(
            "secure benchmark output could not be written"
        ) from exc


def execute(
    env: Mapping[str, str],
    *,
    case_executor: Callable[..., dict[str, Any]] | None = None,
    provider_factory: Callable[..., tuple[Any, Any]] = _provider_factory,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if case_executor is None:
        raise LocalProductionBenchmarkError(
            "concrete production case executor is unavailable until bound"
        )
    config = _load_config(env)
    executor_provenance = _executor_provenance(case_executor)
    started = clock()
    deadline = started + config["max_duration_seconds"]
    try:
        pack = LocalBenchmarkPack.open(
            Path(env["CV_LOCAL_EVAL_PACK_ROOT"]),
            expected_bundle_sha256=config["dataset_sha256"],
            expected_records=config["records"],
            expected_query_types=config["query_types"],
        )
        initial_pack = bundle_descriptor(Path(env["CV_LOCAL_EVAL_PACK_ROOT"]))
        executions = pack.execution_projection()
        if pack.execution_sha256 is None:
            raise LocalProductionBenchmarkError(
                "private benchmark execution projection is unbound"
            )
        config["execution_sha256"] = _sha(
            pack.execution_sha256, name="execution projection"
        )
    except Exception as exc:  # noqa: BLE001 - private-pack boundary
        raise LocalProductionBenchmarkError(
            "private benchmark pack admission failed"
        ) from exc
    classification_rank = {"internal": 0, "confidential": 1, "restricted": 2}
    if any(
        classification_rank[document["classification"]]
        > classification_rank[config["classification"]]
        for case in executions
        for document in case["documents"]
    ):
        raise LocalProductionBenchmarkError(
            "private pack document exceeds approved classification"
        )
    ledger = _ObservedLedger(config)
    try:
        embedding, generation = provider_factory(config, ledger.observe)
    except Exception as exc:  # noqa: BLE001 - provider construction boundary
        raise LocalProductionBenchmarkError("exact local model loading failed") from exc
    initial_models = _provider_attestation(embedding, generation)
    observations: list[dict[str, Any]] = []
    usage: dict[str, Any] | None = None
    executor_attestation: dict[str, str] | None = None
    lifecycle_error: Exception | None = None
    prepare = getattr(case_executor, "prepare", None)
    finalize = getattr(case_executor, "finalize", None)
    phase_one = getattr(case_executor, "phase_one", None)
    phase_two = getattr(case_executor, "phase_two", None)
    phased = callable(phase_one) and callable(phase_two)
    embedding_closed = False

    def duration_gate() -> None:
        if clock() > deadline:
            raise LocalProductionBenchmarkError(
                "benchmark duration budget exceeded"
            )

    def close_embedding_for_phase_transition() -> None:
        nonlocal embedding_closed
        close = getattr(embedding, "close", None)
        if not callable(close):
            raise LocalProductionBenchmarkError(
                "phased embedding provider close hook is unavailable"
            )
        try:
            close()
            embedding_closed = True
            _cleanup_local_model_memory()
        except LocalProductionBenchmarkError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider cleanup boundary
            raise LocalProductionBenchmarkError(
                "local provider close failed"
            ) from exc

    try:
        if prepare is not None:
            if not callable(prepare):
                raise LocalProductionBenchmarkError(
                    "production executor prepare hook is invalid"
                )
            prepare(config, embedding, generation)
        executor_attestation = _executor_attestation(case_executor)
        if phased:
            handles: deque[tuple[Mapping[str, Any], Any]] = deque()
            for case in executions:
                case_id = _execution_view(case)["id"]
                duration_gate()
                ledger.select(case_id)
                try:
                    handle = phase_one(
                        case,
                        embedding_provider=embedding,
                        observe_request=ledger.observe,
                    )
                except LocalProductionBenchmarkError:
                    raise
                except Exception as exc:  # noqa: BLE001 - production boundary
                    raise LocalProductionBenchmarkError(
                        "production retrieval phase failed"
                    ) from exc
                handles.append((case, handle))
                duration_gate()

            close_embedding_for_phase_transition()
            while handles:
                case, handle = handles.popleft()
                case_id = _execution_view(case)["id"]
                duration_gate()
                ledger.select(case_id)
                try:
                    raw = phase_two(
                        case,
                        handle=handle,
                        generation_client=generation,
                        observe_request=ledger.observe,
                    )
                except LocalProductionBenchmarkError:
                    raise
                except Exception as exc:  # noqa: BLE001 - production boundary
                    raise LocalProductionBenchmarkError(
                        "production answer phase failed"
                    ) from exc
                observations.append(_observation(raw, case_id))
                duration_gate()
        else:
            for case in executions:
                case_id = _execution_view(case)["id"]
                duration_gate()
                ledger.select(case_id)
                try:
                    raw = case_executor(
                        case,
                        embedding_provider=embedding,
                        generation_client=generation,
                        observe_request=ledger.observe,
                    )
                except LocalProductionBenchmarkError:
                    raise
                except Exception as exc:  # noqa: BLE001 - production executor boundary
                    raise LocalProductionBenchmarkError(
                        "production case execution failed"
                    ) from exc
                observations.append(_observation(raw, case_id))
                duration_gate()
        if _executor_attestation(case_executor) != executor_attestation:
            raise LocalProductionBenchmarkError(
                "production executor attestation changed during execution"
            )
        if bundle_descriptor(Path(env["CV_LOCAL_EVAL_PACK_ROOT"])) != initial_pack:
            raise LocalProductionBenchmarkError(
                "private benchmark pack changed during execution"
            )
        try:
            final_models = _provider_attestation(embedding, generation)
        except LocalProductionBenchmarkError as exc:
            raise LocalProductionBenchmarkError(
                "local model snapshot changed during execution"
            ) from exc
        if final_models != initial_models:
            raise LocalProductionBenchmarkError(
                "local model snapshot changed during execution"
            )
        usage = _usage(embedding, generation, ledger.calls)
    except Exception as exc:  # finalized below before the failure is surfaced
        lifecycle_error = exc
    finally:
        ledger.seal_requests()
        if finalize is not None:
            if not callable(finalize):
                if lifecycle_error is None:
                    lifecycle_error = LocalProductionBenchmarkError(
                        "production executor finalize hook is invalid"
                    )
            else:
                try:
                    finalize()
                except Exception as exc:  # noqa: BLE001 - executor cleanup boundary
                    if lifecycle_error is None:
                        lifecycle_error = LocalProductionBenchmarkError(
                            "production executor finalize failed"
                        )
                        lifecycle_error.__cause__ = exc
        providers_to_close = [generation]
        if not embedding_closed:
            providers_to_close.append(embedding)
        for provider in providers_to_close:
            close = getattr(provider, "close", None)
            if close is not None:
                if not callable(close):
                    if lifecycle_error is None:
                        lifecycle_error = LocalProductionBenchmarkError(
                            "local provider close hook is invalid"
                        )
                else:
                    try:
                        close()
                    except Exception as exc:  # noqa: BLE001 - provider cleanup boundary
                        if lifecycle_error is None:
                            lifecycle_error = LocalProductionBenchmarkError(
                                "local provider close failed"
                            )
                            lifecycle_error.__cause__ = exc
    if lifecycle_error is not None:
        if isinstance(lifecycle_error, LocalProductionBenchmarkError):
            raise lifecycle_error
        raise LocalProductionBenchmarkError(
            "production benchmark lifecycle failed"
        ) from lifecycle_error
    duration_gate()
    assert usage is not None
    assert executor_attestation is not None
    ledger_records = ledger.records(_execution_view(case)["id"] for case in executions)
    try:
        pack.freeze_request_ledger(ledger_records)
        # This is the first point at which labels are parsed or exposed to scoring.
        labels = pack.load_labels()
        final_pack = bundle_descriptor(Path(env["CV_LOCAL_EVAL_PACK_ROOT"]))
    except Exception as exc:  # noqa: BLE001 - private-pack boundary
        raise LocalProductionBenchmarkError(
            "private benchmark pack changed or failed its frozen-ledger gate"
        ) from exc
    if final_pack != initial_pack:
        raise LocalProductionBenchmarkError(
            "private benchmark pack changed during execution"
        )
    if clock() > deadline:
        raise LocalProductionBenchmarkError("benchmark duration budget exceeded")
    metrics, breakdown = _score(executions, labels, observations)
    for used, limit in (
        (usage["provider_calls"], config["max_provider_calls"]),
        (usage["input_tokens"], config["max_input_tokens"]),
        (usage["output_tokens"], config["max_output_tokens"]),
        (usage["cost_usd"], config["max_cost_usd"]),
    ):
        if used > limit:
            raise LocalProductionBenchmarkError("local provider usage budget exceeded")
    report = {
        "schema_version": "2.0",
        "embedding_provider": EMBEDDING_PROVIDER,
        "embedding_model": BGE_IDENTITY,
        "generation_provider": GENERATION_PROVIDER,
        "generation_model": QWEN_IDENTITY,
        "embedding_profile_hash": executor_attestation["embedding_profile_hash"],
        "prompt_hash": executor_attestation["prompt_hash"],
        "config_hash": _hash(
            {
                "tool_version": TOOL_VERSION,
                "dataset_sha256": config["dataset_sha256"],
                "request_ledger_sha256": pack.request_ledger_sha256,
                "executor": executor_provenance,
                "pipeline_config_hash": executor_attestation["pipeline_config_hash"],
                "model_residency_strategy": executor_provenance.get(
                    "model_residency_strategy", "single-phase-fallback"
                ),
                "generation_max_output_tokens": (
                    LOCAL_GENERATION_MAX_OUTPUT_TOKENS
                ),
                "model_device": "mps",
                "redis_role": "health-only",
                "database_isolation_prefix": "cv3_eval_",
                "bucket_isolation_prefix": "cv3-eval-",
            },
            domain="context-vault/local-production-benchmark-config/v1",
        ),
        "environment_hash": env["CV_EVAL_ENVIRONMENT_HASH"],
        "metrics": metrics,
        "usage": usage,
        "query_type_breakdown": breakdown,
        # Candidate assertion only; independent non-transfer evidence is a later gate.
        "golden_results_sent_to_provider": False,
    }
    _write_exclusive(config["output"], report)
    return report


def main() -> int:
    try:
        execute(dict(os.environ), case_executor=production_case_executor)
    except LocalProductionBenchmarkError as exc:
        print(f"local production benchmark failed closed: {exc}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - never disclose raw runtime inputs on CLI failure
        print(
            "local production benchmark failed closed: unexpected runtime boundary failure",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
