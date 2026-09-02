"""A9 tier, dataset and regression-gate structural guarantees."""

from __future__ import annotations

import importlib.util
import json
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
            "latency_ms": {"p95": 100},
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
            "latency_ms": {"p95": 121},
        }
    }
    findings = run_eval._compare(current, baseline_path)
    assert "recall@5 regressed by more than 0.02" in findings
    assert "citation_coverage regressed by more than 0.02" in findings
    assert "p95 latency regressed by more than 20 percent" in findings


def benchmark_fixture(tmp_path, monkeypatch):
    manifest = {
        "schema_version": "1.0",
        "opaque_pack_id": "synthetic-test-pack",
        "dataset_sha256": "a" * 64,
        "classification": "internal",
        "reviewed_by": "synthetic-test-reviewer",
        "approved_at_utc": "2026-09-02T00:00:00Z",
    }
    report = {
        "provider": "synthetic-test-only",
        "model": "not-a-real-model",
        "embedding_profile_hash": "b" * 64,
        "prompt_hash": "c" * 64,
        "config_hash": "d" * 64,
        "metrics": {
            "permission_version_leakage": 0,
            "invalid_citation_labels": 0,
            "fabricated_no_answer_responses": 0,
            "critical_high_security_findings": 0,
            "recall@5": 0.9,
            "mrr@10": 0.8,
            "citation_precision": 1.0,
            "citation_coverage": 1.0,
            "latency_ms": {"p95": 100.0},
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    args = SimpleNamespace(
        approval_id="synthetic-test-approval",
        private_pack_manifest=path,
        provider_runner=["NEVER_EXECUTE"],
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
    "field", ["approval_id", "private_pack_manifest", "provider_runner"]
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
