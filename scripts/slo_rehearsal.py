#!/usr/bin/env python3
"""Produce a local-only synthetic SLO and alert-delivery rehearsal receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 1024 * 1024
MAX_SAMPLES = 10_000
POLICY_SCHEMA = "context-vault-slo-provisional-policy/v1"
SAMPLE_SCHEMA = "context-vault-slo-synthetic-samples/v1"
RECEIPT_SCHEMA = "context-vault-slo-rehearsal-receipt/v1"
CANONICAL_POLICY_STATUS = "PROVISIONAL_BLOCKED_PENDING_BASELINE"
METRICS = {
    "api_availability_ratio": ("min", 0.0, 1.0),
    "api_p95_latency_ms": ("max", 0.0, 86_400_000.0),
    "ingestion_success_ratio": ("min", 0.0, 1.0),
    "ingestion_p95_seconds": ("max", 0.0, 604_800.0),
    "outbox_oldest_age_seconds": ("max", 0.0, 31_536_000.0),
    "backup_age_seconds": ("max", 0.0, 31_536_000.0),
    "restore_drill_success_ratio": ("min", 0.0, 1.0),
    "restore_drill_age_seconds": ("max", 0.0, 31_536_000.0),
    "benchmark_regression_ratio": ("max", 0.0, 1.0),
    "permission_leakage_count": ("max", 0.0, 10_000.0),
    "invalid_citation_count": ("max", 0.0, 10_000.0),
}
SAMPLE_FIELDS = {
    "api_available",
    "api_latency_ms",
    "ingestion_completed",
    "ingestion_duration_seconds",
    "outbox_oldest_age_seconds",
    "backup_age_seconds",
    "restore_drill_succeeded",
    "restore_drill_age_seconds",
    "benchmark_regression",
    "permission_leakage_count",
    "invalid_citation_count",
}


class SloRehearsalError(RuntimeError):
    pass


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SloRehearsalError("input JSON contains duplicate keys")
        result[key] = value
    return result


def stable_json(path: Path) -> tuple[dict[str, Any], str]:
    """Read one regular, non-symlink JSON input through a stable descriptor."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SloRehearsalError("input is unavailable or unsafe") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 0 < before.st_size <= MAX_INPUT_BYTES
        ):
            raise SloRehearsalError("input must be a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, min(64 * 1024, MAX_INPUT_BYTES + 1 - total)):
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                raise SloRehearsalError("input exceeds size policy")
            chunks.append(chunk)
        after = os.fstat(fd)
        binding = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if binding != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise SloRehearsalError("input changed during stable read")
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    try:
        decoded = json.loads(raw, object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SloRehearsalError("input is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise SloRehearsalError("input root must be an object")
    return decoded, hashlib.sha256(raw).hexdigest()


def _number(value: Any, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SloRehearsalError("metric value must be numeric")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise SloRehearsalError("metric value exceeds bounded policy") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise SloRehearsalError("metric value exceeds bounded policy")
    return result


def _count(value: Any, *, high: int) -> int:
    result = _number(value, low=0, high=high)
    if not result.is_integer():
        raise SloRehearsalError("count metric must be an integer")
    return int(result)


def canonical_thresholds(payload: dict[str, Any]) -> dict[str, float]:
    required_root = {
        "schema_version",
        "policy_id",
        "status",
        "baseline_receipt_sha256",
        "maximum_measurement_age_seconds",
        "on_call",
        "slos",
    }
    if set(payload) != required_root or payload.get("schema_version") != "1.0":
        raise SloRehearsalError("canonical SLO policy schema is malformed")
    if (
        payload.get("status") != CANONICAL_POLICY_STATUS
        or payload.get("baseline_receipt_sha256") is not None
    ):
        raise SloRehearsalError("canonical SLO policy is not provisional")
    slos = payload.get("slos")
    if not isinstance(slos, list) or len(slos) != 11:
        raise SloRehearsalError("canonical SLO policy set is incomplete")
    by_name = {}
    for slo in slos:
        if not isinstance(slo, dict) or set(slo) != {
            "name",
            "comparator",
            "threshold",
            "owner",
            "escalation",
            "runbook",
        }:
            raise SloRehearsalError("canonical SLO entry is malformed")
        name = slo.get("name")
        if not isinstance(name, str) or name in by_name:
            raise SloRehearsalError("canonical SLO names are malformed")
        by_name[name] = slo
    bindings = {
        "api_availability_percent": ("api_availability_ratio", "at_least", 0.01),
        "api_p95_latency_ms": ("api_p95_latency_ms", "at_most", 1.0),
        "ingestion_completion_p95_seconds": (
            "ingestion_p95_seconds",
            "at_most",
            1.0,
        ),
        "outbox_oldest_age_seconds": (
            "outbox_oldest_age_seconds",
            "at_most",
            1.0,
        ),
        "permission_leakage_count": ("permission_leakage_count", "exactly", 1.0),
        "invalid_citation_count": ("invalid_citation_count", "exactly", 1.0),
        "backup_age_seconds": ("backup_age_seconds", "at_most", 1.0),
        "restore_drill_age_seconds": (
            "restore_drill_age_seconds",
            "at_most",
            1.0,
        ),
        "restore_drill_success_percent": (
            "restore_drill_success_ratio",
            "at_least",
            0.01,
        ),
        "real_benchmark_regression_percent": (
            "benchmark_regression_ratio",
            "at_most",
            0.01,
        ),
    }
    if set(by_name) != {*bindings, "ingestion_failure_percent"}:
        raise SloRehearsalError("canonical SLO names drifted")
    result = {}
    for source, (target, comparator, scale) in bindings.items():
        slo = by_name[source]
        if slo.get("comparator") != comparator:
            raise SloRehearsalError("canonical SLO comparator drifted")
        result[target] = round(
            _number(slo.get("threshold"), low=0, high=100_000_000) * scale,
            12,
        )
    failure = by_name["ingestion_failure_percent"]
    if failure.get("comparator") != "at_most":
        raise SloRehearsalError("canonical ingestion SLO comparator drifted")
    failure_percent = _number(failure.get("threshold"), low=0, high=100)
    result["ingestion_success_ratio"] = 1.0 - failure_percent / 100
    return result


def validate_policy(
    payload: dict[str, Any],
    canonical_payload: dict[str, Any],
    *,
    canonical_sha256: str,
) -> dict[str, float]:
    if set(payload) != {
        "schema",
        "authority",
        "canonical_policy_sha256",
        "thresholds",
    }:
        raise SloRehearsalError("policy fields are not allow-listed")
    if (
        payload["schema"] != POLICY_SCHEMA
        or payload["authority"] != "provisional_local_only"
    ):
        raise SloRehearsalError("policy cannot grant production authority")
    if payload["canonical_policy_sha256"] != canonical_sha256:
        raise SloRehearsalError("provisional policy canonical binding drifted")
    thresholds = payload["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != set(METRICS):
        raise SloRehearsalError("policy threshold set is incomplete")
    normalized = {
        name: _number(thresholds[name], low=bounds[1], high=bounds[2])
        for name, bounds in METRICS.items()
    }
    if normalized != canonical_thresholds(canonical_payload):
        raise SloRehearsalError("provisional thresholds drifted from canonical policy")
    return normalized


def validate_samples(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if set(payload) != {"schema", "classification", "samples"}:
        raise SloRehearsalError("sample fields are not allow-listed")
    if payload["schema"] != SAMPLE_SCHEMA or payload["classification"] != "synthetic":
        raise SloRehearsalError("only explicitly synthetic samples are accepted")
    samples = payload["samples"]
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SAMPLES:
        raise SloRehearsalError("sample count exceeds bounded policy")
    normalized: list[dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != SAMPLE_FIELDS:
            raise SloRehearsalError("sample metric set is incomplete")
        if (
            not isinstance(sample["api_available"], bool)
            or not isinstance(sample["ingestion_completed"], bool)
            or not isinstance(sample["restore_drill_succeeded"], bool)
            or not isinstance(sample["benchmark_regression"], bool)
        ):
            raise SloRehearsalError("sample status fields must be boolean")
        normalized.append(
            {
                **sample,
                "api_latency_ms": _number(
                    sample["api_latency_ms"], low=0, high=86_400_000
                ),
                "ingestion_duration_seconds": _number(
                    sample["ingestion_duration_seconds"], low=0, high=604_800
                ),
                "outbox_oldest_age_seconds": _number(
                    sample["outbox_oldest_age_seconds"], low=0, high=31_536_000
                ),
                "backup_age_seconds": _number(
                    sample["backup_age_seconds"], low=0, high=31_536_000
                ),
                "restore_drill_age_seconds": _number(
                    sample["restore_drill_age_seconds"], low=0, high=31_536_000
                ),
                "permission_leakage_count": _count(
                    sample["permission_leakage_count"], high=1
                ),
                "invalid_citation_count": _count(
                    sample["invalid_citation_count"], high=1
                ),
            }
        )
    return normalized


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 6)


def evaluate(samples: list[dict[str, Any]]) -> dict[str, float]:
    count = len(samples)
    return {
        "api_availability_ratio": round(
            sum(item["api_available"] for item in samples) / count, 6
        ),
        "api_p95_latency_ms": _p95([item["api_latency_ms"] for item in samples]),
        "ingestion_success_ratio": round(
            sum(item["ingestion_completed"] for item in samples) / count, 6
        ),
        "ingestion_p95_seconds": _p95(
            [item["ingestion_duration_seconds"] for item in samples]
        ),
        "outbox_oldest_age_seconds": max(
            item["outbox_oldest_age_seconds"] for item in samples
        ),
        "backup_age_seconds": max(item["backup_age_seconds"] for item in samples),
        "restore_drill_success_ratio": round(
            sum(item["restore_drill_succeeded"] for item in samples) / count, 6
        ),
        "restore_drill_age_seconds": max(
            item["restore_drill_age_seconds"] for item in samples
        ),
        "benchmark_regression_ratio": round(
            sum(item["benchmark_regression"] for item in samples) / count, 6
        ),
        "permission_leakage_count": sum(
            item["permission_leakage_count"] for item in samples
        ),
        "invalid_citation_count": sum(
            item["invalid_citation_count"] for item in samples
        ),
    }


def _utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise SloRehearsalError("operational receipt timestamp is malformed")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SloRehearsalError("operational receipt timestamp is malformed") from exc
    if parsed.tzinfo is None:
        raise SloRehearsalError("operational receipt timestamp is not UTC")
    return parsed.astimezone(timezone.utc)


def operational_ages(
    backup: dict[str, Any], restore: dict[str, Any], *, now: datetime
) -> dict[str, float]:
    """Project only ages from successful, non-mutating backup/restore receipts."""
    backup_contract = {
        "receipt_type": "backup-complete",
        "status": "PASS",
        "release_gate_eligible": True,
        "source_mutated": False,
        "credential_values_retained": False,
        "raw_object_names_retained_in_receipt": False,
    }
    restore_contract = {
        "receipt_type": "fresh-target-restore-drill",
        "status": "PASS",
        "release_gate_eligible": True,
        "fresh_target_verified": True,
        "source_mutated": False,
        "credential_values_retained": False,
        "raw_object_names_retained": False,
    }
    if any(backup.get(key) != value for key, value in backup_contract.items()):
        raise SloRehearsalError("backup receipt is not eligible for age metrics")
    if any(restore.get(key) != value for key, value in restore_contract.items()):
        raise SloRehearsalError("restore receipt is not eligible for age metrics")
    if now.tzinfo is None:
        raise SloRehearsalError("operational observation time must be UTC")
    completed = _utc_timestamp(backup.get("completed_at_utc"))
    restored = _utc_timestamp(restore.get("restore_finished_at_utc"))
    result = {}
    for name, timestamp in (
        ("backup.age_seconds", completed),
        ("restore_drill.age_seconds", restored),
    ):
        age = (now.astimezone(timezone.utc) - timestamp).total_seconds()
        result[name] = _number(age, low=0, high=10 * 365 * 24 * 60 * 60)
    return result


def build_receipt(
    policy: dict[str, Any],
    canonical_policy: dict[str, Any],
    samples_payload: dict[str, Any],
    *,
    policy_sha256: str,
    canonical_policy_sha256: str,
    samples_sha256: str,
    generated_at: datetime | None = None,
    expect_alert: bool = False,
    operational_observation: dict[str, float] | None = None,
    operational_source_binding: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not all(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in (policy_sha256, canonical_policy_sha256, samples_sha256)
    ):
        raise SloRehearsalError("source binding is malformed")
    if operational_source_binding is not None:
        expected_binding = {
            "backup_receipt_sha256",
            "restore_receipt_sha256",
        }
        if set(operational_source_binding) != expected_binding or not all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in operational_source_binding.values()
        ):
            raise SloRehearsalError("operational source binding is malformed")
    if (operational_observation is None) != (operational_source_binding is None):
        raise SloRehearsalError("operational observation binding is incomplete")
    if operational_observation is not None:
        expected_observation = {
            "backup.age_seconds",
            "restore_drill.age_seconds",
        }
        if set(operational_observation) != expected_observation:
            raise SloRehearsalError("operational observation fields are malformed")
        operational_observation = {
            name: _number(
                operational_observation[name],
                low=0,
                high=10 * 365 * 24 * 60 * 60,
            )
            for name in expected_observation
        }
    thresholds = validate_policy(
        policy,
        canonical_policy,
        canonical_sha256=canonical_policy_sha256,
    )
    samples = validate_samples(samples_payload)
    baseline = evaluate(samples)
    if operational_observation is not None:
        baseline["backup_age_seconds"] = operational_observation["backup.age_seconds"]
        baseline["restore_drill_age_seconds"] = operational_observation[
            "restore_drill.age_seconds"
        ]
    alerts = []
    for metric, (direction, _low, _high) in METRICS.items():
        observed = baseline[metric]
        threshold = thresholds[metric]
        breached = observed < threshold if direction == "min" else observed > threshold
        if breached:
            alerts.append(
                {
                    "metric": metric,
                    "comparison": direction,
                    "observed": observed,
                    "threshold": threshold,
                }
            )
    if expect_alert and not alerts:
        raise SloRehearsalError("expected alert was not produced")
    timestamp = generated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise SloRehearsalError("receipt timestamp must be timezone-aware")
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "generated_at_utc": timestamp.astimezone(timezone.utc).isoformat(),
        "classification": "synthetic",
        "authority": "provisional_local_only",
        "production_baseline": False,
        "sample_count": len(samples),
        "source_binding": {
            "policy_sha256": policy_sha256,
            "canonical_policy_sha256": canonical_policy_sha256,
            "samples_sha256": samples_sha256,
        },
        "baseline": baseline,
        "evaluation": {
            "state": "ALERT" if alerts else "OK",
            "alert_count": len(alerts),
        },
        "alerts": alerts,
        "delivery": {
            "mode": "local_receipt_only",
            "attempted": True,
            "delivered": True,
            "remote_delivery": False,
        },
        "operational_observation": operational_observation,
        "operational_source_binding": operational_source_binding,
        "production_owner_approval": False,
        "sensitive_fields_retained": False,
    }


def write_new(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if len(encoded) > MAX_INPUT_BYTES:
        raise SloRehearsalError("receipt exceeds size policy")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise SloRehearsalError("receipt output is unavailable or unsafe") from exc
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SloRehearsalError("receipt write did not complete")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--canonical-policy", type=Path, required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--expect-alert", action="store_true")
    parser.add_argument("--backup-receipt", type=Path)
    parser.add_argument("--restore-receipt", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        policy, policy_hash = stable_json(args.policy)
        canonical_policy, canonical_policy_hash = stable_json(args.canonical_policy)
        samples, samples_hash = stable_json(args.samples)
        if (args.backup_receipt is None) != (args.restore_receipt is None):
            raise SloRehearsalError(
                "backup and restore receipts must be supplied together"
            )
        generated_at = datetime.now(timezone.utc)
        observation = None
        observation_binding = None
        if args.backup_receipt is not None and args.restore_receipt is not None:
            backup, backup_hash = stable_json(args.backup_receipt)
            restore, restore_hash = stable_json(args.restore_receipt)
            observation = operational_ages(backup, restore, now=generated_at)
            observation_binding = {
                "backup_receipt_sha256": backup_hash,
                "restore_receipt_sha256": restore_hash,
            }
        receipt = build_receipt(
            policy,
            canonical_policy,
            samples,
            policy_sha256=policy_hash,
            canonical_policy_sha256=canonical_policy_hash,
            samples_sha256=samples_hash,
            generated_at=generated_at,
            expect_alert=args.expect_alert,
            operational_observation=observation,
            operational_source_binding=observation_binding,
        )
        write_new(args.json_output, receipt)
    except (OSError, SloRehearsalError):
        print("SLO rehearsal failed closed", file=sys.stderr)
        return 2
    except Exception:
        print("SLO rehearsal failed closed", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
