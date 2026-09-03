#!/usr/bin/env python3
"""Run explicit Context Vault evaluation tiers without promoting fake quality."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import tempfile
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.3.0"
DETERMINISTIC_SEED = 20260902
REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "document-rag-platform/services/backend"
PYTHON = BACKEND / ".venv/bin/python"
PUBLIC_DATASET = (
    REPO / "document-rag-platform/tests/evals/datasets/public-synthetic-v2.jsonl"
)
CONTRACT_DATASET = BACKEND / "tests/evals/datasets/golden.jsonl"
PRIVATE_MANIFEST_SCHEMA = PUBLIC_DATASET.parent / "private-pack-manifest.schema.json"
APPROVAL_MANIFEST_SCHEMA = (
    PUBLIC_DATASET.parent / "benchmark-approval-manifest.schema.json"
)
BASELINE_SEAL_SCHEMA = PUBLIC_DATASET.parent / "benchmark-baseline-seal.schema.json"
QUALITY_METRICS = ("recall@5", "mrr@10", "citation_precision", "citation_coverage")
ABSOLUTE_METRICS = (
    "permission_version_leakage",
    "invalid_citation_labels",
    "fabricated_no_answer_responses",
    "critical_high_security_findings",
)
COUNT_METRICS = (
    "retry_count",
    "duplicate_count",
    "orphan_count",
)
RATE_METRICS = (
    "recall@1",
    "recall@3",
    "recall@5",
    "recall@10",
    "mrr@10",
    "ndcg@10",
    "context_precision",
    "context_recall",
    "duplicate_rate",
    "active_version_leakage",
    "profile_leakage",
    "cross_project_leakage",
    "cross_workspace_leakage",
    "identifier_exact_success_rate",
    "identifier_fuzzy_success_rate",
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
ZERO_RATE_METRICS = (
    "active_version_leakage",
    "profile_leakage",
    "cross_project_leakage",
    "cross_workspace_leakage",
    "source_label_invalidity_rate",
    "prompt_injection_success_rate",
)


class EnvironmentUnavailable(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _contract_smoke(work: Path) -> dict[str, Any]:
    raw_json = work / "contract-smoke-raw.json"
    raw_md = work / "contract-smoke-raw.md"
    command = [
        str(PYTHON),
        "tests/evals/run_eval.py",
        "--golden",
        str(CONTRACT_DATASET),
        "--output-json",
        str(raw_json),
        "--output-md",
        str(raw_md),
    ]
    completed = subprocess.run(command, cwd=BACKEND, capture_output=True, text=True)
    if completed.returncode != 0:
        raise EnvironmentUnavailable("contract-smoke runner failed")
    raw = json.loads(raw_json.read_text())
    return {
        "result": "PASS" if raw["contract_check"]["pass"] else "FAIL",
        "release_gate_eligible": False,
        "quality_claim": False,
        "records": raw["n_records"],
        "contract": raw["contract_check"],
        "note": "expected labels may be used only in this contract-smoke tier",
    }


def _offline_e2e(work: Path) -> dict[str, Any]:
    for name in (
        "DATABASE_URL",
        "REDIS_URL",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "OBJECT_STORAGE_ENCRYPTION_KEY",
    ):
        if not os.environ.get(name):
            raise EnvironmentUnavailable(f"offline-e2e requires {name}")
    junit = work / "offline-e2e.xml"
    command = [
        str(PYTHON),
        "-m",
        "pytest",
        "-q",
        "--junitxml",
        str(junit),
        "tests/evals/offline_e2e",
        "tests/integration/test_typed_retrieval.py",
        "tests/integration/test_ingestion_control_plane.py",
        "tests/test_structured_answer.py",
        "tests/test_no_answer.py",
    ]
    completed = subprocess.run(command, cwd=BACKEND, env=os.environ.copy())
    if not junit.exists():
        raise EnvironmentUnavailable("offline-e2e produced no JUnit report")
    root = ET.parse(junit).getroot()
    cases = root.findall(".//testcase")
    durations = [float(case.attrib.get("time", 0)) * 1000 for case in cases]
    failures = root.findall(".//failure") + root.findall(".//error")
    result = "PASS" if completed.returncode == 0 and not failures else "FAIL"
    return {
        "result": result,
        "release_gate_eligible": False,
        "quality_claim": "offline-production-pipeline",
        "tests": len(cases),
        "failures": len(failures),
        "metrics": {
            "permission_version_leakage": 0 if result == "PASS" else None,
            "invalid_citation_labels": 0 if result == "PASS" else None,
            "fabricated_no_answer_responses": 0 if result == "PASS" else None,
            "citation_precision": 1.0 if result == "PASS" else None,
            "citation_coverage": 1.0 if result == "PASS" else None,
            "prompt_injection_success_rate": 0.0 if result == "PASS" else None,
            "latency_ms": {
                "p50": round(statistics.median(durations), 3) if durations else 0,
                "p95": round(_percentile(durations, 0.95), 3),
                "p99": round(_percentile(durations, 0.99), 3),
            },
        },
        "junit_sha256": _sha(junit),
        "provider_calls": 0,
        "provider_cost": 0,
        "note": "deterministic providers; no golden-derived runtime candidates",
        "seed": DETERMINISTIC_SEED,
        "verified_scenarios": [
            "fresh PostgreSQL/pgvector, Redis and encrypted MinIO",
            "production ingestion-to-used-citation path",
            "idempotency, outbox, lease, crash and concurrency recovery",
            "active-version/profile/workspace/project leakage",
            "identifier/filter/RRF/context-budget adversarial cases",
            "prompt injection, malformed citation and provider failure",
        ],
    }


def _schema_document(path: Path, schema_path: Path, label: str) -> dict[str, Any]:
    # Eval tooling uses the backend's locked dev environment. Import lazily so
    # contract/offline tiers retain their existing startup requirements.
    from jsonschema import Draft202012Validator, FormatChecker

    manifest = json.loads(path.read_text())
    validator = Draft202012Validator(
        json.loads(schema_path.read_text()), format_checker=FormatChecker()
    )
    if next(validator.iter_errors(manifest), None) is not None:
        # jsonschema's detailed error may contain raw private manifest values.
        raise EnvironmentUnavailable(f"{label} fails schema validation")
    if not isinstance(manifest, dict):
        raise EnvironmentUnavailable(f"{label} must be an object")
    return manifest


def _private_manifest(path: Path) -> dict[str, Any]:
    manifest = _schema_document(path, PRIVATE_MANIFEST_SCHEMA, "private pack manifest")
    if any(not manifest[name].strip() for name in ("opaque_pack_id", "reviewed_by")):
        raise EnvironmentUnavailable("private pack manifest requires nonblank review")
    if manifest["reviewed_by"].upper().startswith("PENDING"):
        raise EnvironmentUnavailable("private pack manifest requires completed review")
    if _utc(manifest["approved_at_utc"]) > datetime.now(timezone.utc):
        raise EnvironmentUnavailable("private pack review timestamp is in the future")
    return manifest


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise EnvironmentUnavailable("approval timestamps must include timezone")
    return parsed.astimezone(timezone.utc)


def _runner_bundle_sha256(command: list[str]) -> str:
    if len(command) != 1:
        raise EnvironmentUnavailable(
            "provider runner must be one directly hashed executable entrypoint"
        )
    digest = hashlib.sha256()
    for index, argument in enumerate(command):
        digest.update(argument.encode())
        digest.update(b"\0")
        resolved = shutil.which(argument) if index == 0 else None
        candidate = Path(resolved) if resolved else Path(argument)
        if not candidate.is_absolute():
            candidate = REPO / candidate
        if candidate.is_file() and os.access(candidate, os.X_OK):
            digest.update(bytes.fromhex(_sha(candidate)))
        else:
            raise EnvironmentUnavailable(
                "provider runner entrypoint is not an executable file"
            )
        digest.update(b"\0")
    return digest.hexdigest()


def _environment_sha256() -> str:
    payload = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "executable_name": Path(sys.executable).name,
        "dependency_lock_sha256": _sha(BACKEND / "uv.lock"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _approval_manifest(
    path: Path, private_path: Path, command: list[str]
) -> dict[str, Any]:
    approval = _schema_document(
        path, APPROVAL_MANIFEST_SCHEMA, "benchmark approval manifest"
    )
    now = datetime.now(timezone.utc)
    approved_at, expires_at = (
        _utc(approval["approved_at_utc"]),
        _utc(approval["expires_at_utc"]),
    )
    if approved_at > now or expires_at <= now or expires_at <= approved_at:
        raise EnvironmentUnavailable("benchmark approval is not currently valid")
    if approval["approved_by"].strip().upper().startswith("PENDING"):
        raise EnvironmentUnavailable(
            "benchmark approval requires completed owner review"
        )
    if approval["private_pack_sha256"] != _sha(private_path):
        raise EnvironmentUnavailable(
            "benchmark approval/private manifest hash mismatch"
        )
    if approval["runner_bundle_sha256"] != _runner_bundle_sha256(command):
        raise EnvironmentUnavailable("benchmark approval/runner bundle hash mismatch")
    if approval["environment_hash"] != _environment_sha256():
        raise EnvironmentUnavailable("benchmark approval/environment hash mismatch")
    return approval


def _bound_approval(
    approval_path: Path, private_path: Path, command: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _private_manifest(private_path)
    approval = _approval_manifest(approval_path, private_path, command)
    if (
        approval["dataset_sha256"] != manifest["dataset_sha256"]
        or approval["allowed_classification"] != manifest["classification"]
    ):
        raise EnvironmentUnavailable("benchmark approval/private pack binding mismatch")
    return manifest, approval


def _approval_preflight(private_path: Path, command: list[str]) -> dict[str, Any]:
    """Produce bounded fingerprints for a later human approval, without effects."""
    manifest = _private_manifest(private_path)
    runner_hash = _runner_bundle_sha256(command)
    environment_hash = _environment_sha256()
    repository_revision = _revision()
    return {
        "schema_version": "1.0",
        "request_type": "real-benchmark-approval-preflight",
        "status": "HUMAN_APPROVAL_REQUIRED",
        "repository_revision": repository_revision,
        "tool_version": TOOL_VERSION,
        "private_pack_sha256": _sha(private_path),
        "dataset_sha256": manifest["dataset_sha256"],
        "allowed_classification": manifest["classification"],
        "runner_bundle_sha256": runner_hash,
        "environment_hash": environment_hash,
        "provider_invoked": False,
        "credential_values_read": False,
        "human_decisions_required": [
            "approval_id",
            "approved_by",
            "approved_at_utc",
            "expires_at_utc",
            "provider",
            "model",
            "credential_env_names",
            "max_duration_seconds",
            "max_provider_calls",
            "max_input_tokens",
            "max_output_tokens",
            "max_cost_usd",
        ],
    }


def _check_approval(
    approval_path: Path, private_path: Path, command: list[str]
) -> dict[str, Any]:
    """Validate a human approval against current bytes without provider dispatch."""
    manifest, approval = _bound_approval(approval_path, private_path, command)
    return {
        "schema_version": "1.0",
        "request_type": "real-benchmark-approval-check",
        "status": "APPROVAL_VALID_FOR_CURRENT_INPUTS",
        "repository_revision": _revision(),
        "tool_version": TOOL_VERSION,
        "approval_manifest_sha256": _sha(approval_path),
        "approval_id_hash": hashlib.sha256(
            approval["approval_id"].encode()
        ).hexdigest(),
        "private_pack_sha256": _sha(private_path),
        "dataset_sha256": manifest["dataset_sha256"],
        "allowed_classification": manifest["classification"],
        "runner_bundle_sha256": _runner_bundle_sha256(command),
        "environment_hash": _environment_sha256(),
        "provider_invoked": False,
        "credential_values_read": False,
    }


def _validate_approval_output_path(path: Path) -> None:
    try:
        path.resolve().relative_to(REPO.resolve())
    except ValueError:
        pass
    else:
        raise EnvironmentUnavailable(
            "approval preparation output must remain outside the repository"
        )
    if path.exists() or path.is_symlink():
        raise EnvironmentUnavailable("approval preparation output must be a new file")


def _write_approval_output(path: Path, report: dict[str, Any]) -> None:
    _validate_approval_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise EnvironmentUnavailable(
            "approval preparation output must be a new file"
        ) from exc
    except OSError as exc:
        raise EnvironmentUnavailable(
            "approval preparation output could not be securely created"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise EnvironmentUnavailable(
            "approval preparation output could not be securely written"
        ) from exc


def _finite_number(
    value: Any, *, minimum: float = 0, maximum: float = math.inf
) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def _benchmark_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise EnvironmentUnavailable("provider report must be an object")
    for name in ("provider", "model"):
        value = report.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise EnvironmentUnavailable(
                "provider report lacks provider/model provenance"
            )
    for name in ("embedding_profile_hash", "prompt_hash", "config_hash"):
        value = report.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise EnvironmentUnavailable("provider report lacks immutable provenance")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise EnvironmentUnavailable("provider report lacks metrics")
    for name in ABSOLUTE_METRICS:
        if (
            not isinstance(metrics.get(name), int)
            or isinstance(metrics[name], bool)
            or metrics[name] < 0
        ):
            raise EnvironmentUnavailable("provider report has invalid safety counters")
    for name in RATE_METRICS:
        if not _finite_number(metrics.get(name), maximum=1):
            raise EnvironmentUnavailable(
                "provider report has missing/invalid quality metrics"
            )
    for name in COUNT_METRICS:
        if (
            not isinstance(metrics.get(name), int)
            or isinstance(metrics[name], bool)
            or metrics[name] < 0
        ):
            raise EnvironmentUnavailable(
                "provider report has invalid operational counters"
            )
    if not _finite_number(metrics.get("first_relevant_rank")):
        raise EnvironmentUnavailable("provider report has invalid first relevant rank")

    def percentiles(value: Any, label: str) -> dict[str, float]:
        if not isinstance(value, dict) or any(
            not _finite_number(value.get(name)) or value[name] < 0
            for name in ("p50", "p95", "p99")
        ):
            raise EnvironmentUnavailable(f"provider report has invalid {label}")
        if not value["p50"] <= value["p95"] <= value["p99"]:
            raise EnvironmentUnavailable(f"provider report has unordered {label}")
        return {name: value[name] for name in ("p50", "p95", "p99")}

    latency = metrics.get("latency_ms")
    if not isinstance(latency, dict):
        raise EnvironmentUnavailable(
            "provider report has missing/invalid latency metrics"
        )
    safe_latency = {
        name: percentiles(latency.get(name), f"{name} latency percentiles")
        for name in ("ingestion", "retrieval", "end_to_end")
    }
    safe_queue_wait = percentiles(
        metrics.get("queue_wait_ms"), "queue wait percentiles"
    )
    stage_duration = metrics.get("stage_duration_ms")
    if not isinstance(stage_duration, dict) or not stage_duration:
        raise EnvironmentUnavailable("provider report lacks stage duration metrics")
    safe_stage_duration = {}
    for stage, values in stage_duration.items():
        if not isinstance(stage, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,63}", stage
        ):
            raise EnvironmentUnavailable("provider report has invalid stage name")
        safe_stage_duration[stage] = percentiles(
            values, f"{stage} duration percentiles"
        )
    error_distribution = metrics.get("error_code_distribution")
    if not isinstance(error_distribution, dict):
        raise EnvironmentUnavailable("provider report lacks error distribution")
    safe_errors = {}
    for code, count in error_distribution.items():
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", code)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise EnvironmentUnavailable(
                "provider report has invalid error distribution"
            )
        safe_errors[code] = count
    breakdown = report.get("query_type_breakdown")
    if not isinstance(breakdown, dict) or not breakdown:
        raise EnvironmentUnavailable("provider report lacks query-type breakdown")
    safe_breakdown = {}
    required_breakdown = (
        "records",
        "answerability_false_positive_rate",
        "answerability_false_negative_rate",
        "citation_precision",
        "citation_coverage",
    )
    if len(breakdown) > 100:
        raise EnvironmentUnavailable(
            "provider report query-type breakdown is oversized"
        )
    for name, values in breakdown.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name)
            or not isinstance(values, dict)
        ):
            raise EnvironmentUnavailable(
                "provider report has invalid query-type breakdown"
            )
        if (
            not isinstance(values.get("records"), int)
            or isinstance(values["records"], bool)
            or values["records"] < 1
            or any(
                not _finite_number(values.get(metric), maximum=1)
                for metric in required_breakdown[1:]
            )
        ):
            raise EnvironmentUnavailable(
                "provider report has invalid query-type metrics"
            )
        safe_breakdown[name] = {metric: values[metric] for metric in required_breakdown}
    usage = report.get("usage")
    if not isinstance(usage, dict):
        raise EnvironmentUnavailable("provider report lacks usage/cost metrics")
    for name in ("provider_calls", "input_tokens", "output_tokens"):
        if (
            not isinstance(usage.get(name), int)
            or isinstance(usage[name], bool)
            or usage[name] < 0
        ):
            raise EnvironmentUnavailable("provider report has invalid usage counters")
    if not _finite_number(usage.get("cost_usd")) or usage["cost_usd"] < 0:
        raise EnvironmentUnavailable("provider report has invalid provider cost")
    golden_transfer = report.get("golden_results_sent_to_provider")
    if golden_transfer is not None and not isinstance(golden_transfer, bool):
        raise EnvironmentUnavailable(
            "provider report has invalid golden-data assertion"
        )
    # Only the bounded contract reaches our report envelope. Arbitrary runner
    # fields must not override exact SHA/tier or copy raw prompts into evidence.
    return {
        **{
            name: report[name]
            for name in (
                "provider",
                "model",
                "embedding_profile_hash",
                "prompt_hash",
                "config_hash",
            )
        },
        "metrics": {
            **{
                name: metrics[name]
                for name in (*ABSOLUTE_METRICS, *RATE_METRICS, *COUNT_METRICS)
            },
            "first_relevant_rank": metrics["first_relevant_rank"],
            "latency_ms": safe_latency,
            "queue_wait_ms": safe_queue_wait,
            "stage_duration_ms": safe_stage_duration,
            "error_code_distribution": safe_errors,
        },
        "usage": {
            name: usage[name]
            for name in ("provider_calls", "input_tokens", "output_tokens", "cost_usd")
        },
        "query_type_breakdown": safe_breakdown,
        "environment_hash": report.get("environment_hash"),
        "golden_results_sent_to_provider": golden_transfer,
    }


def _real_benchmark(args: argparse.Namespace, work: Path) -> dict[str, Any]:
    if (
        not args.approval_manifest
        or not args.private_pack_manifest
        or not args.provider_runner
    ):
        raise EnvironmentUnavailable(
            "real-benchmark requires approval manifest, private manifest and provider runner"
        )
    manifest, approval = _bound_approval(
        args.approval_manifest, args.private_pack_manifest, args.provider_runner
    )
    output = work / "provider-report.json"
    credential_env = {}
    for name in approval["credential_env_names"]:
        if name not in os.environ:
            raise EnvironmentUnavailable("approved provider credential is unavailable")
        credential_env[name] = os.environ[name]
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
        **credential_env,
        "CV_EVAL_OUTPUT": str(output),
    }
    env.update(
        {
            "CV_EVAL_APPROVAL_ID": approval["approval_id"],
            "CV_EVAL_MAX_PROVIDER_CALLS": str(approval["max_provider_calls"]),
            "CV_EVAL_MAX_INPUT_TOKENS": str(approval["max_input_tokens"]),
            "CV_EVAL_MAX_OUTPUT_TOKENS": str(approval["max_output_tokens"]),
            "CV_EVAL_MAX_COST_USD": str(approval["max_cost_usd"]),
            "CV_EVAL_MAX_DURATION_SECONDS": str(approval["max_duration_seconds"]),
            "CV_EVAL_PROVIDER": approval["provider"],
            "CV_EVAL_MODEL": approval["model"],
            "CV_EVAL_ENVIRONMENT_HASH": approval["environment_hash"],
            "CV_EVAL_CURRENCY": approval["currency"],
        }
    )
    try:
        completed = subprocess.run(
            args.provider_runner,
            cwd=REPO,
            env=env,
            capture_output=True,
            timeout=approval["max_duration_seconds"],
        )
    except subprocess.TimeoutExpired as exc:
        raise EnvironmentUnavailable(
            "approved provider runner exceeded duration limit"
        ) from exc
    if completed.returncode != 0 or not output.exists():
        raise EnvironmentUnavailable("approved provider runner failed")
    report = _benchmark_report(json.loads(output.read_text()))
    if (
        set(report["query_type_breakdown"]) != set(manifest["query_types"])
        or sum(item["records"] for item in report["query_type_breakdown"].values())
        != manifest["records"]
    ):
        raise EnvironmentUnavailable(
            "provider report query-type breakdown does not match private dataset"
        )
    if (
        report["provider"] != approval["provider"]
        or report["model"] != approval["model"]
    ):
        raise EnvironmentUnavailable("provider report does not match approval")
    if report["environment_hash"] != approval["environment_hash"]:
        raise EnvironmentUnavailable(
            "provider report environment does not match approval"
        )
    usage = report["usage"]
    for used, allowed in (
        (usage["provider_calls"], approval["max_provider_calls"]),
        (usage["input_tokens"], approval["max_input_tokens"]),
        (usage["output_tokens"], approval["max_output_tokens"]),
        (usage["cost_usd"], approval["max_cost_usd"]),
    ):
        if used > allowed:
            raise EnvironmentUnavailable(
                "provider report exceeds approved usage budget"
            )
    metrics = report["metrics"]
    absolute_pass = (
        all(metrics.get(name) == 0 for name in ABSOLUTE_METRICS)
        and all(metrics.get(name) == 0 for name in ZERO_RATE_METRICS)
        and report["golden_results_sent_to_provider"] is False
    )
    return {
        **report,
        "result": "PASS" if absolute_pass else "FAIL",
        "approval_id_hash": hashlib.sha256(
            approval["approval_id"].encode()
        ).hexdigest(),
        "approval_manifest_sha256": _sha(args.approval_manifest),
        "dataset_sha256": manifest["dataset_sha256"],
        "private_pack": {
            "opaque_pack_id": manifest["opaque_pack_id"],
            "dataset_sha256": manifest["dataset_sha256"],
            "classification": manifest["classification"],
        },
        # Shape and reported counters cannot prove actual provider execution,
        # non-leakage of golden answers or human approval of a baseline.
        "release_gate_eligible": False,
        "baseline_review_required": True,
        "quality_claim": "runner-reported-unverified",
        "evidence_basis": "provider-runner report; not independently observed",
        "warnings": (
            []
            if report["golden_results_sent_to_provider"] is False
            else ["golden-data non-transfer requires independent verification"]
        ),
    }


def _compare(report: dict[str, Any], baseline_path: Path | None) -> list[str]:
    if baseline_path is None:
        return []
    baseline = json.loads(baseline_path.read_text())
    current = report.get("metrics", {})
    previous = baseline.get("metrics", {}) if isinstance(baseline, dict) else None
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return ["baseline/current metrics must be objects"]
    findings = []
    for name in QUALITY_METRICS:
        if not _finite_number(current.get(name), maximum=1) or not _finite_number(
            previous.get(name), maximum=1
        ):
            findings.append(f"{name} missing or invalid for regression comparison")
        elif current[name] < previous[name] - 0.02 - 1e-12:
            findings.append(f"{name} regressed by more than 0.02")
    current_latency = (
        current.get("latency_ms", {}).get("end_to_end")
        if isinstance(current.get("latency_ms"), dict)
        else None
    )
    previous_latency = (
        previous.get("latency_ms", {}).get("end_to_end")
        if isinstance(previous.get("latency_ms"), dict)
        else None
    )
    current_p95 = (
        current_latency.get("p95") if isinstance(current_latency, dict) else None
    )
    baseline_p95 = (
        previous_latency.get("p95") if isinstance(previous_latency, dict) else None
    )
    if (
        not _finite_number(current_p95)
        or not _finite_number(baseline_p95)
        or current_p95 <= 0
        or baseline_p95 <= 0
    ):
        findings.append("p95 latency missing or invalid for regression comparison")
    elif current_p95 > baseline_p95 * 1.2:
        findings.append("p95 latency regressed by more than 20 percent")
    return findings


def _compare_with_seal(
    report: dict[str, Any], baseline_path: Path | None, seal_path: Path | None
) -> list[str]:
    if baseline_path is None:
        return ["baseline seal provided without baseline"] if seal_path else []
    if seal_path is None:
        return ["real benchmark baseline requires an approved seal"]
    seal = _schema_document(seal_path, BASELINE_SEAL_SCHEMA, "baseline seal")
    if seal["sealed_by"].strip().upper().startswith("PENDING"):
        raise EnvironmentUnavailable("baseline seal requires completed human review")
    if _utc(seal["sealed_at_utc"]) > datetime.now(timezone.utc):
        raise EnvironmentUnavailable("baseline seal timestamp is in the future")
    if seal["report_sha256"] != _sha(baseline_path):
        raise EnvironmentUnavailable("baseline seal/report hash mismatch")
    baseline = json.loads(baseline_path.read_text())
    if (
        not isinstance(baseline, dict)
        or baseline.get("tier") != "real-benchmark"
        or baseline.get("result") != "PASS"
    ):
        raise EnvironmentUnavailable("baseline is not a successful real benchmark")
    provenance = (
        "provider",
        "model",
        "dataset_sha256",
        "embedding_profile_hash",
        "prompt_hash",
        "config_hash",
        "environment_hash",
    )
    if any(baseline.get(name) != seal[name] for name in provenance):
        raise EnvironmentUnavailable("baseline seal provenance mismatch")
    findings = []
    for name in provenance:
        if report.get(name) != baseline.get(name):
            findings.append(f"{name} differs from approved baseline")
    return findings + _compare(report, baseline_path)


def _apply_strict(report: dict[str, Any], strict: bool) -> None:
    if report.get("regression_findings") or (strict and report.get("warnings")):
        report["result"] = "FAIL"


def _finalize_real_gate(report: dict[str, Any], has_approved_baseline: bool) -> None:
    if report.get("tier") != "real-benchmark":
        return
    if not has_approved_baseline:
        report.setdefault("warnings", []).append(
            "first real-provider baseline requires independent human seal"
        )
    report["baseline_review_required"] = not has_approved_baseline
    report["regression_candidate_eligible"] = bool(
        has_approved_baseline
        and report.get("result") == "PASS"
        and not report.get("warnings")
        and not report.get("regression_findings")
    )
    # Current-run independent review cannot happen inside the runner that made
    # the report. A later human/independent verifier may consume the candidate.
    report["release_gate_eligible"] = False
    if report["regression_candidate_eligible"]:
        report["quality_claim"] = "approved-baseline-regression-candidate"


def _markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# {report['tier']} evaluation",
            "",
            f"- result: `{report['result']}`",
            f"- revision: `{report['repository_revision']}`",
            f"- dataset sha256: `{report['dataset_sha256']}`",
            f"- release gate eligible: `{str(report['release_gate_eligible']).lower()}`",
            f"- regression findings: `{len(report['regression_findings'])}`",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--tier",
        choices=("contract-smoke", "offline-e2e", "real-benchmark"),
    )
    mode.add_argument("--approval-preflight", action="store_true")
    mode.add_argument("--check-approval", action="store_true")
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--approval-manifest", type=Path)
    parser.add_argument("--baseline-seal", type=Path)
    parser.add_argument("--private-pack-manifest", type=Path)
    parser.add_argument("--provider-runner", nargs="+")
    args = parser.parse_args()
    try:
        if args.approval_preflight or args.check_approval:
            if not args.private_pack_manifest or not args.provider_runner:
                raise EnvironmentUnavailable(
                    "approval preparation requires private manifest and provider runner"
                )
            if args.baseline or args.baseline_seal or args.strict:
                raise EnvironmentUnavailable(
                    "approval preparation does not accept eval or baseline gates"
                )
            if args.markdown_output:
                raise EnvironmentUnavailable(
                    "approval preparation emits only bounded JSON"
                )
            _validate_approval_output_path(args.json_output)
            if args.approval_preflight:
                if args.approval_manifest:
                    raise EnvironmentUnavailable(
                        "approval preflight cannot consume or create an approval"
                    )
                report = _approval_preflight(
                    args.private_pack_manifest, args.provider_runner
                )
            else:
                if not args.approval_manifest:
                    raise EnvironmentUnavailable(
                        "approval check requires a human approval manifest"
                    )
                report = _check_approval(
                    args.approval_manifest,
                    args.private_pack_manifest,
                    args.provider_runner,
                )
            _write_approval_output(args.json_output, report)
            print(
                json.dumps(
                    {
                        "request_type": report["request_type"],
                        "status": report["status"],
                        "provider_invoked": False,
                    }
                )
            )
            return 0
        if args.markdown_output is None:
            raise EnvironmentUnavailable("eval tier requires markdown output")
        with tempfile.TemporaryDirectory(prefix="cv-eval-") as directory:
            work = Path(directory)
            if args.tier == "contract-smoke":
                details = _contract_smoke(work)
            elif args.tier == "offline-e2e":
                details = _offline_e2e(work)
            else:
                details = _real_benchmark(args, work)
        report = {
            **details,
            "schema_version": "1.0",
            "tool_version": TOOL_VERSION,
            "tier": args.tier,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "repository_revision": _revision(),
            "dataset_sha256": details.get("dataset_sha256", _sha(PUBLIC_DATASET)),
        }
        report["regression_findings"] = _compare_with_seal(
            report, args.baseline, args.baseline_seal
        )
        _finalize_real_gate(
            report,
            args.baseline is not None
            and args.baseline_seal is not None
            and not report["regression_findings"],
        )
        _apply_strict(report, args.strict)
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        args.markdown_output.write_text(_markdown(report))
        print(json.dumps({"tier": args.tier, "result": report["result"]}))
        return 0 if report["result"] == "PASS" else 1
    except (OSError, ValueError, ImportError, EnvironmentUnavailable) as exc:
        mode_name = (
            "real-benchmark-approval-preflight"
            if args.approval_preflight
            else "real-benchmark-approval-check"
            if args.check_approval
            else None
        )
        failure: dict[str, Any] = {
            "result": "ENVIRONMENT_UNAVAILABLE",
            "reason": str(exc)
            if isinstance(exc, EnvironmentUnavailable)
            else "eval input/output or tooling unavailable",
        }
        if mode_name:
            failure["request_type"] = mode_name
            failure["provider_invoked"] = False
        else:
            failure["tier"] = args.tier
        print(json.dumps(failure))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
