"""A9 tier, dataset and regression-gate structural guarantees."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


REPO = Path(__file__).resolve().parents[5]
SCRIPT = REPO / "scripts/run_eval.py"
SPEC = importlib.util.spec_from_file_location("cv_run_eval", SCRIPT)
assert SPEC and SPEC.loader
run_eval = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_eval)


def test_public_dataset_has_reviewable_v2_contract_and_required_coverage():
    path = REPO / "document-rag-platform/tests/evals/datasets/public-synthetic-v2.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    required = {
        "id",
        "query",
        "intent",
        "answerable",
        "workspace_fixture",
        "project_fixture",
        "expected_facts",
        "expected_source_constraints",
        "forbidden_sources",
        "permission_persona",
        "query_type",
        "language",
        "adversarial_tags",
        "notes",
        "reviewer",
        "dataset_version",
        "split",
    }
    assert len(rows) == 16
    assert all(required <= set(row) for row in rows)
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row["split"] for row in rows} == {"train", "holdout"}
    required_types = {
        "code",
        "table",
        "identifier",
        "prose",
        "ocr",
        "archive",
        "repository",
        "multi-document",
        "contradictory-source",
        "temporal-version",
        "no-answer",
        "permission",
    }
    assert required_types <= {row["query_type"] for row in rows}


def test_offline_fixture_does_not_read_golden_expected_results():
    fixture = (
        REPO
        / "document-rag-platform/services/backend/tests/evals/offline_e2e/test_pipeline.py"
    ).read_text()
    assert "expected_facts" not in fixture
    assert "expected_source_constraints" not in fixture
    assert "forbidden_sources" not in fixture


def test_contract_smoke_is_never_release_gate_eligible(tmp_path):
    report = run_eval._contract_smoke(tmp_path)
    assert report["result"] == "PASS"
    assert report["quality_claim"] is False
    assert report["release_gate_eligible"] is False


def test_regression_comparator_enforces_quality_and_latency_budgets(tmp_path):
    baseline = {
        "metrics": {
            "recall@5": 0.9,
            "mrr@10": 0.8,
            "citation_precision": 0.95,
            "citation_coverage": 0.9,
            "latency_ms": {"end_to_end": {"p50": 50, "p95": 100, "p99": 120}},
        }
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    current = {
        "metrics": {
            "recall@5": 0.87,
            "mrr@10": 0.8,
            "citation_precision": 0.95,
            "citation_coverage": 0.87,
            "latency_ms": {"end_to_end": {"p50": 50, "p95": 121, "p99": 130}},
        }
    }
    findings = run_eval._compare(current, baseline_path)
    assert "recall@5 regressed by more than 0.02" in findings
    assert "citation_coverage regressed by more than 0.02" in findings
    assert "p95 latency regressed by more than 20 percent" in findings


def benchmark_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "memory-only-test-key")
    manifest = {
        "schema_version": "1.0",
        "opaque_pack_id": "synthetic-test-pack",
        "dataset_sha256": "a" * 64,
        "classification": "internal",
        "reviewed_by": "synthetic-test-reviewer",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "records": 1,
        "query_types": ["prose"],
    }
    report = {
        "provider": "synthetic-test-only",
        "model": "not-a-real-model",
        "embedding_profile_hash": "b" * 64,
        "prompt_hash": "c" * 64,
        "config_hash": "d" * 64,
        "environment_hash": run_eval._environment_sha256(),
        "metrics": {
            "permission_version_leakage": 0,
            "invalid_citation_labels": 0,
            "fabricated_no_answer_responses": 0,
            "critical_high_security_findings": 0,
            **{name: 0.9 for name in run_eval.RATE_METRICS},
            **{name: 0.0 for name in run_eval.ZERO_RATE_METRICS},
            **{name: 1 for name in run_eval.COUNT_METRICS},
            "first_relevant_rank": 1.5,
            "latency_ms": {
                name: {"p50": 50.0, "p95": 100.0, "p99": 120.0}
                for name in ("ingestion", "retrieval", "end_to_end")
            },
            "queue_wait_ms": {"p50": 1.0, "p95": 2.0, "p99": 3.0},
            "stage_duration_ms": {"retrieval": {"p50": 5.0, "p95": 10.0, "p99": 12.0}},
            "error_code_distribution": {},
        },
        "usage": {
            "provider_calls": 16,
            "input_tokens": 1600,
            "output_tokens": 800,
            "cost_usd": 1.25,
        },
        "query_type_breakdown": {
            "prose": {
                "records": 1,
                "answerability_false_positive_rate": 0.0,
                "answerability_false_negative_rate": 0.0,
                "citation_precision": 1.0,
                "citation_coverage": 1.0,
            }
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    entrypoint = tmp_path / "synthetic-runner"
    entrypoint.write_text("#!/bin/sh\nexit 99\n")
    entrypoint.chmod(0o700)
    command = [str(entrypoint)]
    approval = {
        "schema_version": "1.0",
        "approval_id": "synthetic-test-approval",
        "approved_by": "synthetic-owner",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "expires_at_utc": "2027-09-02T00:00:00Z",
        "provider": report["provider"],
        "model": report["model"],
        "private_pack_sha256": run_eval._sha(path),
        "dataset_sha256": manifest["dataset_sha256"],
        "allowed_classification": manifest["classification"],
        "runner_bundle_sha256": run_eval._runner_bundle_sha256(command),
        "environment_hash": report["environment_hash"],
        "credential_env_names": ["SYNTHETIC_PROVIDER_KEY"],
        "max_duration_seconds": 30,
        "max_provider_calls": 20,
        "max_input_tokens": 2000,
        "max_output_tokens": 1000,
        "max_cost_usd": 2.0,
        "currency": "USD",
    }
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(approval))
    args = SimpleNamespace(
        approval_manifest=approval_path,
        private_pack_manifest=path,
        provider_runner=command,
    )

    def local_stub(*_args, **_kwargs):
        (tmp_path / "provider-report.json").write_text(json.dumps(report))
        return SimpleNamespace(returncode=0)

    runner = Mock(side_effect=local_stub)
    monkeypatch.setattr(run_eval.subprocess, "run", runner)
    return args, manifest, report, runner


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "wrong"},
        {"dataset_sha256": "not-a-hash"},
        {"classification": "public"},
        {"reviewed_by": ""},
        {"approved_at_utc": "not-a-date"},
        {"unexpected": "private-detail"},
    ],
)
def test_invalid_private_manifest_never_dispatches_provider(
    tmp_path, monkeypatch, change
):
    args, manifest, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    manifest.update(change)
    args.private_pack_manifest.write_text(json.dumps(manifest))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


def test_empty_security_only_report_cannot_pass_quality_gate(tmp_path, monkeypatch):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    for name in (
        "recall@5",
        "mrr@10",
        "citation_precision",
        "citation_coverage",
        "latency_ms",
    ):
        report["metrics"].pop(name)
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)


def test_runner_report_is_not_automatically_sealed_or_claimed_observed(
    tmp_path, monkeypatch
):
    args, _, _, _ = benchmark_fixture(tmp_path, monkeypatch)
    result = run_eval._real_benchmark(args, tmp_path)
    assert result["release_gate_eligible"] is False
    assert result["golden_results_sent_to_provider"] is None
    assert result["baseline_review_required"] is True


@pytest.mark.parametrize(
    "value", [None, True, float("nan"), float("inf"), -1, 2, "0.9"]
)
def test_non_numeric_or_nonfinite_quality_metric_is_rejected(
    tmp_path, monkeypatch, value
):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    report["metrics"]["recall@5"] = value
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)


def test_provider_cannot_override_envelope_or_leak_extra_fields(tmp_path, monkeypatch):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    report.update(
        repository_revision="fake",
        tier="fake",
        raw_prompt="SECRET",
        release_gate_eligible=True,
    )
    result = run_eval._real_benchmark(args, tmp_path)
    assert "repository_revision" not in result and "tier" not in result
    assert "SECRET" not in json.dumps(result)
    assert result["release_gate_eligible"] is False


def test_missing_regression_metrics_are_a_failure_not_a_silent_pass(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"metrics": {"recall@5": 0.9}}))
    assert run_eval._compare({"metrics": {}}, baseline)


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "opaque_pack_id",
        "dataset_sha256",
        "classification",
        "reviewed_by",
        "approved_at_utc",
        "records",
        "query_types",
    ],
)
def test_each_required_manifest_field_is_enforced_before_effect(
    tmp_path, monkeypatch, field
):
    args, manifest, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    manifest.pop(field)
    args.private_pack_manifest.write_text(json.dumps(manifest))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


@pytest.mark.parametrize(
    "field", ["approval_manifest", "private_pack_manifest", "provider_runner"]
)
def test_missing_admission_never_dispatches_provider(tmp_path, monkeypatch, field):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    setattr(args, field, None)
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


@pytest.mark.parametrize("value", [None, [], "private-text", True])
def test_nonobject_manifest_is_rejected_before_dispatch(tmp_path, monkeypatch, value):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    args.private_pack_manifest.write_text(json.dumps(value))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", ""),
        ("model", None),
        ("prompt_hash", "fake"),
        ("metrics", None),
        ("golden_results_sent_to_provider", "false"),
    ],
)
def test_malformed_provider_provenance_is_rejected(tmp_path, monkeypatch, field, value):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    report[field] = value
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)


def test_golden_transfer_is_a_failure_and_unknown_fields_never_escape(
    tmp_path, monkeypatch
):
    args, manifest, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    report["golden_results_sent_to_provider"] = True
    report["metrics"]["raw_context"] = "SECRET"
    result = run_eval._real_benchmark(args, tmp_path)
    assert result["result"] == "FAIL"
    assert result["golden_results_sent_to_provider"] is True
    assert result["dataset_sha256"] == manifest["dataset_sha256"]
    assert "SECRET" not in json.dumps(result)
    assert runner.call_args.kwargs["capture_output"] is True
    assert runner.call_args.kwargs["timeout"] == 30
    child_env = runner.call_args.kwargs["env"]
    assert child_env["SYNTHETIC_PROVIDER_KEY"] == "memory-only-test-key"
    assert child_env["CV_EVAL_PROVIDER"] == report["provider"]
    assert child_env["CV_EVAL_MODEL"] == report["model"]
    assert child_env["CV_EVAL_ENVIRONMENT_HASH"] == report["environment_hash"]
    assert "HOME" not in child_env and "CODEX_SESSION_ID" not in child_env


@pytest.mark.parametrize(
    "query_types,records",
    [(["prose", "code"], 1), (["prose"], 2)],
)
def test_query_type_breakdown_must_cover_private_manifest(
    tmp_path, monkeypatch, query_types, records
):
    args, manifest, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    manifest.update(query_types=query_types, records=records)
    args.private_pack_manifest.write_text(json.dumps(manifest))
    approval = json.loads(args.approval_manifest.read_text())
    approval["private_pack_sha256"] = run_eval._sha(args.private_pack_manifest)
    args.approval_manifest.write_text(json.dumps(approval))
    with pytest.raises(run_eval.EnvironmentUnavailable, match="breakdown"):
        run_eval._real_benchmark(args, tmp_path)


def test_unknown_golden_transfer_or_nonzero_leakage_cannot_pass(tmp_path, monkeypatch):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    assert run_eval._real_benchmark(args, tmp_path)["result"] == "FAIL"
    report["golden_results_sent_to_provider"] = False
    report["metrics"]["cross_workspace_leakage"] = 0.01
    assert run_eval._real_benchmark(args, tmp_path)["result"] == "FAIL"


def test_successful_regression_comparison_requires_complete_valid_metrics(
    tmp_path, monkeypatch
):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(report))
    assert run_eval._compare(report, baseline) == []
    report["metrics"]["recall@5"] = float("nan")
    assert run_eval._compare(report, baseline)
    assert run_eval._finite_number(10**1000) is False


@pytest.mark.parametrize(
    "field,value,pre_dispatch",
    [
        ("provider", "other", False),
        ("model", "other", False),
        ("private_pack_sha256", "0" * 64, True),
        ("dataset_sha256", "0" * 64, True),
        ("allowed_classification", "restricted", True),
        ("runner_bundle_sha256", "0" * 64, True),
        ("environment_hash", "0" * 64, True),
        ("approved_by", "PENDING_OWNER_REVIEW", True),
        ("approved_by", "   ", True),
        ("expires_at_utc", "2025-01-01T00:00:00Z", True),
    ],
)
def test_approval_binding_failure_is_detected_at_earliest_safe_boundary(
    tmp_path, monkeypatch, field, value, pre_dispatch
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    approval = json.loads(args.approval_manifest.read_text())
    approval[field] = value
    args.approval_manifest.write_text(json.dumps(approval))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    assert runner.call_count == (0 if pre_dispatch else 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_calls", 21),
        ("input_tokens", 2001),
        ("output_tokens", 1001),
        ("cost_usd", 2.01),
    ],
)
def test_reported_budget_overrun_is_rejected(tmp_path, monkeypatch, field, value):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    report["usage"][field] = value
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)


def test_timeout_is_bounded_and_redacted(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    runner.side_effect = subprocess.TimeoutExpired("PRIVATE command", 30)
    with pytest.raises(
        run_eval.EnvironmentUnavailable, match="duration limit"
    ) as failure:
        run_eval._real_benchmark(args, tmp_path)
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "missing",
    [
        "recall@1",
        "ndcg@10",
        "context_precision",
        "citation_recall",
        "answer_sufficiency",
        "p50",
        "p99",
        "usage",
        "first_relevant_rank",
        "query_type_breakdown",
    ],
)
def test_full_measurement_contract_is_required(tmp_path, monkeypatch, missing):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    if missing in ("p50", "p99"):
        report["metrics"]["latency_ms"]["end_to_end"].pop(missing)
    elif missing in ("usage", "query_type_breakdown"):
        report.pop(missing)
    else:
        report["metrics"].pop(missing)
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)


def sealed_baseline(tmp_path, report):
    baseline = {
        **report,
        "tier": "real-benchmark",
        "result": "PASS",
        "dataset_sha256": "a" * 64,
    }
    baseline_path = tmp_path / "real-baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    seal = {
        "schema_version": "1.0",
        "seal_id": "synthetic-seal",
        "sealed_by": "synthetic-human-reviewer",
        "sealed_at_utc": "2026-09-02T00:00:00Z",
        "status": "approved",
        "report_sha256": run_eval._sha(baseline_path),
        **{
            name: baseline[name]
            for name in (
                "provider",
                "model",
                "dataset_sha256",
                "embedding_profile_hash",
                "prompt_hash",
                "config_hash",
                "environment_hash",
            )
        },
    }
    seal_path = tmp_path / "baseline-seal.json"
    seal_path.write_text(json.dumps(seal))
    return baseline_path, seal_path


def test_baseline_requires_matching_human_seal_and_provenance(tmp_path, monkeypatch):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    current = {**report, "dataset_sha256": "a" * 64}
    assert run_eval._compare_with_seal(current, baseline, seal) == []
    assert run_eval._compare_with_seal(current, baseline, None)
    changed = {**current, "environment_hash": "0" * 64}
    assert (
        "environment_hash differs from approved baseline"
        in run_eval._compare_with_seal(changed, baseline, seal)
    )


@pytest.mark.parametrize(
    "change",
    [
        {"status": "rejected"},
        {"report_sha256": "0" * 64},
        {"sealed_by": "PENDING_OWNER_REVIEW"},
        {"sealed_by": "   "},
        {"provider": "other"},
        {"sealed_at_utc": "2027-09-02T00:00:00Z"},
        {"unexpected": "raw"},
    ],
)
def test_invalid_or_unbound_baseline_seal_fails_closed(tmp_path, monkeypatch, change):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    value = json.loads(seal.read_text())
    value.update(change)
    seal.write_text(json.dumps(value))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._compare_with_seal(report, baseline, seal)


def test_strict_turns_warnings_into_failure_but_does_not_break_clean_offline_report():
    candidate = {
        "result": "PASS",
        "regression_findings": [],
        "warnings": ["human review needed"],
    }
    run_eval._apply_strict(candidate, True)
    assert candidate["result"] == "FAIL"
    offline = {"result": "PASS", "regression_findings": []}
    run_eval._apply_strict(offline, True)
    assert offline["result"] == "PASS"
    real = {
        "tier": "real-benchmark",
        "result": "PASS",
        "regression_findings": [],
        "warnings": [],
    }
    run_eval._finalize_real_gate(real, True)
    run_eval._apply_strict(real, True)
    assert real["result"] == "PASS"
    assert real["regression_candidate_eligible"] is True
    assert real["release_gate_eligible"] is False
    first = {**real, "result": "PASS", "warnings": []}
    run_eval._finalize_real_gate(first, False)
    run_eval._apply_strict(first, True)
    assert first["result"] == "FAIL"
    assert first["release_gate_eligible"] is False


def test_unordered_latency_percentiles_are_rejected(tmp_path, monkeypatch):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    report["metrics"]["latency_ms"]["retrieval"] = {
        "p50": 100,
        "p95": 50,
        "p99": 25,
    }
    with pytest.raises(run_eval.EnvironmentUnavailable, match="unordered"):
        run_eval._real_benchmark(args, tmp_path)


@pytest.mark.parametrize(
    "command", [["python", "-m", "runner"], ["sh", "-c", "runner"]]
)
def test_transitive_shell_or_module_runner_is_rejected_before_dispatch(
    tmp_path, monkeypatch, command
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    args.provider_runner = command
    with pytest.raises(run_eval.EnvironmentUnavailable, match="one directly hashed"):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()
