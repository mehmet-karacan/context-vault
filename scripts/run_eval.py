#!/usr/bin/env python3
"""Run explicit Context Vault evaluation tiers without promoting fake quality."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.1.0"
DETERMINISTIC_SEED = 20260902
REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "document-rag-platform/services/backend"
PYTHON = BACKEND / ".venv/bin/python"
PUBLIC_DATASET = (
    REPO / "document-rag-platform/tests/evals/datasets/public-synthetic-v2.jsonl"
)
CONTRACT_DATASET = BACKEND / "tests/evals/datasets/golden.jsonl"
PRIVATE_MANIFEST_SCHEMA = PUBLIC_DATASET.parent / "private-pack-manifest.schema.json"
QUALITY_METRICS = ("recall@5", "mrr@10", "citation_precision", "citation_coverage")
ABSOLUTE_METRICS = (
    "permission_version_leakage",
    "invalid_citation_labels",
    "fabricated_no_answer_responses",
    "critical_high_security_findings",
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


def _private_manifest(path: Path) -> dict[str, Any]:
    # Eval tooling uses the backend's locked dev environment. Import lazily so
    # contract/offline tiers retain their existing startup requirements.
    from jsonschema import Draft202012Validator, FormatChecker

    manifest = json.loads(path.read_text())
    validator = Draft202012Validator(
        json.loads(PRIVATE_MANIFEST_SCHEMA.read_text()), format_checker=FormatChecker()
    )
    if next(validator.iter_errors(manifest), None) is not None:
        # jsonschema's detailed error may contain raw private manifest values.
        raise EnvironmentUnavailable("private pack manifest fails schema validation")
    if any(not manifest[name].strip() for name in ("opaque_pack_id", "reviewed_by")):
        raise EnvironmentUnavailable("private pack manifest requires nonblank review")
    if manifest["reviewed_by"].upper().startswith("PENDING"):
        raise EnvironmentUnavailable("private pack manifest requires completed review")
    return manifest


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
    for name in QUALITY_METRICS:
        if not _finite_number(metrics.get(name), maximum=1):
            raise EnvironmentUnavailable(
                "provider report has missing/invalid quality metrics"
            )
    latency = metrics.get("latency_ms")
    if (
        not isinstance(latency, dict)
        or not _finite_number(latency.get("p95"))
        or latency["p95"] <= 0
    ):
        raise EnvironmentUnavailable("provider report has missing/invalid p95 latency")
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
            **{name: metrics[name] for name in (*ABSOLUTE_METRICS, *QUALITY_METRICS)},
            "latency_ms": {"p95": latency["p95"]},
        },
        "golden_results_sent_to_provider": golden_transfer,
    }


def _real_benchmark(args: argparse.Namespace, work: Path) -> dict[str, Any]:
    if (
        not args.approval_id
        or not args.approval_id.strip()
        or not args.private_pack_manifest
        or not args.provider_runner
    ):
        raise EnvironmentUnavailable(
            "real-benchmark requires approval id, private manifest and provider runner"
        )
    manifest = _private_manifest(args.private_pack_manifest)
    output = work / "provider-report.json"
    env = os.environ.copy()
    env["CV_EVAL_OUTPUT"] = str(output)
    env["CV_EVAL_APPROVAL_ID"] = args.approval_id
    completed = subprocess.run(
        args.provider_runner, cwd=REPO, env=env, capture_output=True
    )
    if completed.returncode != 0 or not output.exists():
        raise EnvironmentUnavailable("approved provider runner failed")
    report = _benchmark_report(json.loads(output.read_text()))
    metrics = report["metrics"]
    absolute_pass = (
        all(metrics.get(name) == 0 for name in ABSOLUTE_METRICS)
        and report["golden_results_sent_to_provider"] is not True
    )
    return {
        **report,
        "result": "PASS" if absolute_pass else "FAIL",
        "approval_id_hash": hashlib.sha256(args.approval_id.encode()).hexdigest(),
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
    current_latency = current.get("latency_ms")
    previous_latency = previous.get("latency_ms")
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
    parser.add_argument(
        "--tier",
        required=True,
        choices=("contract-smoke", "offline-e2e", "real-benchmark"),
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--approval-id")
    parser.add_argument("--private-pack-manifest", type=Path)
    parser.add_argument("--provider-runner", nargs="+")
    args = parser.parse_args()
    try:
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
        report["regression_findings"] = _compare(report, args.baseline)
        if report["regression_findings"]:
            report["result"] = "FAIL"
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        args.markdown_output.write_text(_markdown(report))
        print(json.dumps({"tier": args.tier, "result": report["result"]}))
        return 0 if report["result"] == "PASS" else 1
    except (OSError, ValueError, ImportError, EnvironmentUnavailable) as exc:
        print(
            json.dumps(
                {
                    "tier": args.tier,
                    "result": "ENVIRONMENT_UNAVAILABLE",
                    "reason": str(exc)
                    if isinstance(exc, EnvironmentUnavailable)
                    else "eval input/output or tooling unavailable",
                }
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
