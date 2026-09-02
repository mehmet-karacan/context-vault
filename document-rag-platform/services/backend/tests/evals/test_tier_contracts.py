"""A9 tier, dataset and regression-gate structural guarantees."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


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
