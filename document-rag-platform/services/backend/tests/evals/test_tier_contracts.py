"""A9 tier, dataset and regression-gate structural guarantees."""

from __future__ import annotations

import importlib.util
import json
import py_compile
import socket
import subprocess
import sys
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


def _approved_public_review_fixture(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "review-receipt.json"
    dataset.write_bytes(run_eval.PUBLIC_DATASET.read_bytes())
    receipt = {
        "schema_version": "1.0",
        "receipt_type": "public-dataset-human-review",
        "review_id": "synthetic-review-001",
        "decision": "approved",
        "reviewed_by": "synthetic-human-reviewer",
        "reviewer_reference_sha256": "b" * 64,
        "reviewed_at_utc": "2026-09-03T00:00:00Z",
        "dataset_sha256": run_eval._sha(dataset),
        "dataset_version": "2.0.0",
        "records": 16,
        "splits": ["train", "holdout"],
        "review_scope": "all-records-content-labels-and-splits",
    }
    receipt_path.write_text(json.dumps(receipt))
    manifest = json.loads(run_eval.PUBLIC_DATASET_MANIFEST.read_text())
    manifest.update(
        reviewer=receipt["reviewed_by"],
        review_status="approved",
        reviewed_at_utc=receipt["reviewed_at_utc"],
        review_receipt_sha256=run_eval._sha(receipt_path),
    )
    manifest_path.write_text(json.dumps(manifest))
    return dataset, manifest_path, receipt_path, manifest, receipt


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


def test_contract_smoke_reports_the_dataset_it_actually_executes(tmp_path):
    details = run_eval._contract_smoke(tmp_path)

    assert details["dataset_sha256"] == run_eval._sha(run_eval.CONTRACT_DATASET)
    assert details["dataset_sha256"] != run_eval._sha(run_eval.PUBLIC_DATASET)
    assert details["dataset_provenance"] == {
        "kind": "contract-golden-file",
        "records": details["records"],
    }


def test_offline_fixture_bundle_hash_is_deterministic_and_byte_sensitive(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("fixture = 1\n")
    second.write_text("fixture = 2\n")

    initial = run_eval._path_bundle_sha256((first, second), root=tmp_path)
    assert initial == run_eval._path_bundle_sha256((second, first), root=tmp_path)
    second.write_text("fixture = 3\n")
    assert run_eval._path_bundle_sha256((first, second), root=tmp_path) != initial


def test_offline_source_bundle_discovers_directory_tests_and_pytest_support():
    sources = set(run_eval._offline_source_paths())
    discovered = set((run_eval.BACKEND / "tests/evals/offline_e2e").glob("test_*.py"))

    assert discovered
    assert sources == (
        discovered
        | set(run_eval.OFFLINE_EXTRA_TEST_TARGETS)
        | {run_eval.BACKEND / "tests/conftest.py", run_eval.BACKEND / "pyproject.toml"}
    )


def test_offline_source_bundle_recurses_and_includes_nested_conftest(tmp_path):
    relative_paths = [
        run_eval.OFFLINE_E2E_DIRECTORY.relative_to(run_eval.BACKEND) / "test_top.py",
        run_eval.OFFLINE_E2E_DIRECTORY.relative_to(run_eval.BACKEND)
        / "nested/test_deep.py",
        run_eval.OFFLINE_E2E_DIRECTORY.relative_to(run_eval.BACKEND)
        / "nested/conftest.py",
        Path("tests/conftest.py"),
        Path("pyproject.toml"),
        *(
            path.relative_to(run_eval.BACKEND)
            for path in run_eval.OFFLINE_EXTRA_TEST_TARGETS
        ),
    ]
    for relative in relative_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative.as_posix()}\n")

    assert set(run_eval._offline_source_paths(root=tmp_path)) == {
        tmp_path / relative for relative in relative_paths
    }


def test_public_dataset_manifest_is_hash_bound_but_review_stays_pending():
    status = run_eval._public_dataset_status(
        run_eval.PUBLIC_DATASET, run_eval.PUBLIC_DATASET_MANIFEST
    )

    assert status["integrity"] == "PASS"
    assert status["dataset_sha256"] == run_eval._sha(run_eval.PUBLIC_DATASET)
    assert status["dataset_version"] == "2.0.0"
    assert status["records"] == 16
    assert status["splits"] == ["holdout", "train"]
    assert status["review_status"] == "pending"
    assert status["review_complete"] is False
    assert status["review_claim_status"] == "manifest-declared-pending"
    assert status["approval_evidence_verified"] is False
    assert status["release_gate_eligible"] is False
    assert status["provider_invoked"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": "9.9"},
        {"dataset_sha256": "0" * 64},
        {"dataset_version": "9.9.9"},
        {"records": 15},
        {"classification": "internal"},
        {"splits": ["train"]},
        {"review_status": "unknown"},
        {"reviewer": "unverified-name"},
        {"reviewed_at_utc": "2026-09-03T00:00:00Z"},
        {"review_receipt_sha256": "a" * 64},
        {"fixture_independence": ""},
        {"private_equivalent": "other.json"},
        {"unexpected": "field"},
    ],
)
def test_public_dataset_manifest_drift_fails_closed(tmp_path, mutation):
    dataset = tmp_path / "dataset.jsonl"
    manifest_path = tmp_path / "manifest.json"
    dataset.write_bytes(run_eval.PUBLIC_DATASET.read_bytes())
    manifest = json.loads(run_eval.PUBLIC_DATASET_MANIFEST.read_text())
    manifest.update(mutation)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._public_dataset_status(dataset, manifest_path)


def test_public_dataset_row_version_drift_fails_closed(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    manifest_path = tmp_path / "manifest.json"
    rows = [
        json.loads(line) for line in run_eval.PUBLIC_DATASET.read_text().splitlines()
    ]
    rows[0]["dataset_version"] = "9.9.9"
    dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    manifest = json.loads(run_eval.PUBLIC_DATASET_MANIFEST.read_text())
    manifest["dataset_sha256"] = run_eval._sha(dataset)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(run_eval.EnvironmentUnavailable, match="version"):
        run_eval._public_dataset_status(dataset, manifest_path)


def test_public_dataset_self_asserted_approval_never_becomes_verified_review(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    manifest_path = tmp_path / "manifest.json"
    dataset.write_bytes(run_eval.PUBLIC_DATASET.read_bytes())
    manifest = json.loads(run_eval.PUBLIC_DATASET_MANIFEST.read_text())
    manifest.update(
        reviewer="synthetic-human-reviewer",
        review_status="approved",
        reviewed_at_utc="2026-09-03T00:00:00Z",
        review_receipt_sha256="a" * 64,
    )
    manifest_path.write_text(json.dumps(manifest))

    status = run_eval._public_dataset_status(dataset, manifest_path)
    assert status["review_status"] == "approved"
    assert status["review_claim_status"] == "manifest-declared-approved-unverified"
    assert status["approval_evidence_verified"] is False
    assert status["review_complete"] is False
    assert status["release_gate_eligible"] is False
    manifest["reviewed_at_utc"] = "2026-09-03T03:00:00+03:00"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(run_eval.EnvironmentUnavailable, match="must be UTC"):
        run_eval._public_dataset_status(dataset, manifest_path)
    manifest["reviewed_at_utc"] = "2999-01-01T00:00:00Z"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(run_eval.EnvironmentUnavailable, match="timestamp"):
        run_eval._public_dataset_status(dataset, manifest_path)


def test_public_review_receipt_is_exactly_bound_but_does_not_create_authority(
    tmp_path,
):
    dataset, manifest_path, receipt_path, _, _ = _approved_public_review_fixture(
        tmp_path
    )

    status = run_eval._public_dataset_status(dataset, manifest_path, receipt_path)

    assert status["receipt_binding_verified"] is True
    assert status["review_authority_verified"] is False
    assert status["review_complete"] is False
    assert status["review_claim_status"] == (
        "receipt-bound-approved-unverified-authority"
    )
    assert status["provider_invoked"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_sha256", "0" * 64),
        ("dataset_version", "9.9.9"),
        ("records", 15),
        ("splits", ["train"]),
        ("reviewed_by", "different-reviewer"),
        ("reviewed_at_utc", "2026-09-04T00:00:00Z"),
        ("reviewer_reference_sha256", ""),
        ("unexpected", "field"),
    ],
)
def test_public_review_receipt_drift_fails_closed(tmp_path, field, value):
    dataset, manifest_path, receipt_path, manifest, receipt = (
        _approved_public_review_fixture(tmp_path)
    )
    receipt[field] = value
    receipt_path.write_text(json.dumps(receipt))
    manifest["review_receipt_sha256"] = run_eval._sha(receipt_path)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._public_dataset_status(dataset, manifest_path, receipt_path)


def test_public_review_receipt_byte_drift_fails_hash_binding(tmp_path):
    dataset, manifest_path, receipt_path, _, receipt = _approved_public_review_fixture(
        tmp_path
    )
    receipt["review_id"] = "changed-after-manifest-binding"
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(run_eval.EnvironmentUnavailable, match="hash mismatch"):
        run_eval._public_dataset_status(dataset, manifest_path, receipt_path)


def test_pending_public_manifest_rejects_review_receipt(tmp_path):
    receipt_path = tmp_path / "review-receipt.json"
    receipt_path.write_text("{}")

    with pytest.raises(run_eval.EnvironmentUnavailable, match="pending"):
        run_eval._public_dataset_status(
            run_eval.PUBLIC_DATASET,
            run_eval.PUBLIC_DATASET_MANIFEST,
            receipt_path,
        )


def test_public_review_receipt_is_supported_only_by_public_status_cli(
    tmp_path, monkeypatch
):
    dataset, manifest_path, receipt_path, _, _ = _approved_public_review_fixture(
        tmp_path
    )
    output = tmp_path / "status.json"
    monkeypatch.setattr(run_eval, "PUBLIC_DATASET", dataset)
    monkeypatch.setattr(run_eval, "PUBLIC_DATASET_MANIFEST", manifest_path)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--public-dataset-status",
            "--public-review-receipt",
            str(receipt_path),
            "--json-output",
            str(output),
        ],
    )

    assert run_eval.main() == 0
    assert json.loads(output.read_text())["receipt_binding_verified"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--tier",
            "contract-smoke",
            "--public-review-receipt",
            str(receipt_path),
            "--json-output",
            str(tmp_path / "tier.json"),
            "--markdown-output",
            str(tmp_path / "tier.md"),
        ],
    )
    assert run_eval.main() == 3


def test_public_dataset_status_is_a_supported_no_effect_cli_mode(tmp_path, monkeypatch):
    output = tmp_path / "public-dataset-status.json"
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--public-dataset-status",
            "--json-output",
            str(output),
        ],
    )

    assert run_eval.main() == 0
    report = json.loads(output.read_text())
    assert report["schema_version"] == "1.0"
    assert report["review_status"] == "pending"
    assert report["release_gate_eligible"] is False
    assert report["provider_invoked"] is False


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


def test_runner_source_sidecar_binds_full_declared_closure(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "REPO", tmp_path)
    entrypoint = tmp_path / "runner"
    entrypoint.write_text("#!/bin/sh\nexit 0\n")
    entrypoint.chmod(0o700)
    source_root = tmp_path / "src"
    source_root.mkdir()
    source = source_root / "service.py"
    source.write_text("VALUE = 1\n")
    fixed = tmp_path / "uv.lock"
    fixed.write_text("version = 1\n")
    sidecar = entrypoint.with_name(f"{entrypoint.name}.sources.json")
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "python_roots": ["src"],
                "files": ["uv.lock"],
            }
        )
    )

    first = run_eval._runner_bundle_sha256([str(entrypoint)])
    source.write_text("VALUE = 2\n")
    assert run_eval._runner_bundle_sha256([str(entrypoint)]) != first
    source.write_text("VALUE = 1\n")
    (source_root / "new_module.py").write_text("NEW = True\n")
    assert run_eval._runner_bundle_sha256([str(entrypoint)]) != first


@pytest.mark.parametrize(
    "escape_kind", ["file", "python_root", "symlink", "nested_directory_symlink"]
)
def test_runner_source_sidecar_rejects_escape_or_symlink(
    tmp_path, monkeypatch, escape_kind
):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(run_eval, "REPO", repo)
    entrypoint = repo / "runner"
    entrypoint.write_text("#!/bin/sh\nexit 0\n")
    entrypoint.chmod(0o700)
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = True\n")
    sidecar = entrypoint.with_name(f"{entrypoint.name}.sources.json")
    if escape_kind == "file":
        payload = {
            "schema_version": "1.0",
            "python_roots": [],
            "files": ["../outside.py"],
        }
    elif escape_kind == "python_root":
        payload = {
            "schema_version": "1.0",
            "python_roots": [".."],
            "files": [],
        }
    elif escape_kind == "symlink":
        linked = repo / "linked.py"
        linked.symlink_to(outside)
        payload = {
            "schema_version": "1.0",
            "python_roots": [],
            "files": ["linked.py"],
        }
    else:
        source_root = repo / "src"
        source_root.mkdir()
        (source_root / "visible.py").write_text("VALUE = 0\n")
        outside_root = tmp_path / "outside"
        outside_root.mkdir()
        (outside_root / "hidden.py").write_text("VALUE = 1\n")
        (source_root / "nested_link").symlink_to(outside_root, target_is_directory=True)
        payload = {
            "schema_version": "1.0",
            "python_roots": ["src"],
            "files": [],
        }
    sidecar.write_text(json.dumps(payload))
    expected = (
        "contains a symlink"
        if escape_kind == "nested_directory_symlink"
        else "source closure"
    )
    with pytest.raises(run_eval.EnvironmentUnavailable, match=expected):
        run_eval._runner_bundle_sha256([str(entrypoint)])


@pytest.mark.parametrize(
    "relative_artifact",
    [
        "module.pyc",
        "module.pyo",
        "module.so",
        "module.pyd",
        "module.dylib",
        "module.dll",
    ],
)
def test_runner_source_sidecar_rejects_importable_non_source_artifacts(
    tmp_path, monkeypatch, relative_artifact
):
    monkeypatch.setattr(run_eval, "REPO", tmp_path)
    entrypoint = tmp_path / "runner"
    entrypoint.write_text("#!/bin/sh\nexit 0\n")
    entrypoint.chmod(0o700)
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "visible.py").write_text("VALUE = 0\n")
    artifact = source_root / relative_artifact
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"unbound import artifact")
    entrypoint.with_name(f"{entrypoint.name}.sources.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "python_roots": ["src"],
                "files": [],
            }
        )
    )

    with pytest.raises(
        run_eval.EnvironmentUnavailable, match="non-source import artifact"
    ):
        run_eval._runner_bundle_sha256([str(entrypoint)])


def test_runner_source_cache_is_ignored_only_with_isolated_child_pycache(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(run_eval, "REPO", tmp_path)
    entrypoint = tmp_path / "runner"
    entrypoint.write_text("#!/bin/sh\nexit 0\n")
    entrypoint.chmod(0o700)
    source_root = tmp_path / "src"
    source_root.mkdir()
    source = source_root / "victim.py"
    source.write_text("VALUE = 'unapproved'\n")
    cache_file = Path(importlib.util.cache_from_source(str(source)))
    py_compile.compile(
        str(source),
        cfile=str(cache_file),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    source.write_text("VALUE = 'approved-source'\n")
    entrypoint.with_name(f"{entrypoint.name}.sources.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "python_roots": ["src"],
                "files": [],
            }
        )
    )

    first = run_eval._runner_bundle_sha256([str(entrypoint)])
    cache_file.write_bytes(cache_file.read_bytes() + b"ignored-cache-drift")
    assert run_eval._runner_bundle_sha256([str(entrypoint)]) == first

    isolated_cache = tmp_path / "isolated-pycache"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(source_root)!r}); "
                "import victim; print(victim.VALUE)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": run_eval._runner_path(),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": str(isolated_cache),
        },
    )
    assert completed.stdout.strip() == "approved-source"


@pytest.mark.parametrize("name", ["payload.py", "payload.so", "payload.txt"])
def test_runner_source_cache_rejects_non_bytecode_members(tmp_path, monkeypatch, name):
    monkeypatch.setattr(run_eval, "REPO", tmp_path)
    entrypoint = tmp_path / "runner"
    entrypoint.write_text("#!/bin/sh\nexit 0\n")
    entrypoint.chmod(0o700)
    cache = tmp_path / "src" / "__pycache__"
    cache.mkdir(parents=True)
    (tmp_path / "src" / "approved.py").write_text("VALUE = 'approved'\n")
    (cache / name).write_bytes(b"unapproved")
    entrypoint.with_name(f"{entrypoint.name}.sources.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "python_roots": ["src"],
                "files": [],
            }
        )
    )

    with pytest.raises(
        run_eval.EnvironmentUnavailable, match="cache contains a non-bytecode"
    ):
        run_eval._runner_bundle_sha256([str(entrypoint)])


def benchmark_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "memory-only-test-key")
    manifest = {
        "schema_version": "2.0",
        "opaque_pack_id": "synthetic-test-pack",
        "dataset_sha256": "a" * 64,
        "classification": "internal",
        "review_status": "approved",
        "reviewed_by": "synthetic-test-reviewer",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "records": 1,
        "query_types": ["prose"],
    }
    report = {
        "schema_version": "2.0",
        "embedding_provider": "local-sentence-transformers",
        "embedding_model": "BAAI/bge-m3@synthetic-revision",
        "generation_provider": "local-transformers",
        "generation_model": "synthetic-instruct-model@revision",
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
        "schema_version": "3.0",
        "decision": "approved",
        "approval_id": "synthetic-test-approval",
        "approved_by": "synthetic-owner",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "expires_at_utc": "2027-09-02T00:00:00Z",
        "embedding_provider": report["embedding_provider"],
        "embedding_model": report["embedding_model"],
        "generation_provider": report["generation_provider"],
        "generation_model": report["generation_model"],
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


def enable_v3_request_capture(tmp_path, args, manifest, report):
    manifest.update(
        schema_version="3.0",
        golden_dataset_sha256="e" * 64,
        execution_dataset_sha256="f" * 64,
        execution_projection_sha256="1" * 64,
    )
    args.private_pack_manifest.write_text(json.dumps(manifest))
    approval = json.loads(args.approval_manifest.read_text())
    approval["private_pack_sha256"] = run_eval._sha(args.private_pack_manifest)
    args.approval_manifest.write_text(json.dumps(approval))
    report.update(
        request_capture_session_sha256="2" * 64,
        request_capture_ledger_sha256="3" * 64,
        request_capture_request_count=report["usage"]["provider_calls"],
    )
    capture_socket = Path("/tmp") / f"cv-{abs(hash(str(tmp_path))):x}.sock"
    capture_socket.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(capture_socket))
    capture_socket.chmod(0o600)
    nonce_file = tmp_path / "capture.nonce"
    nonce_file.write_text("4" * 64)
    nonce_file.chmod(0o600)
    args.capture_socket = capture_socket
    args.capture_nonce_file = nonce_file
    return listener


def close_v3_request_capture(listener, args):
    socket_path = Path(listener.getsockname())
    listener.close()
    socket_path.unlink(missing_ok=True)


def golden_non_transfer_fixture(tmp_path, monkeypatch):
    args, manifest, provider_report, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    pack_root = tmp_path / "pack"
    golden_dataset = pack_root / "labels/golden.jsonl"
    execution_dataset = pack_root / "execution/cases.jsonl"
    golden_dataset.parent.mkdir(parents=True)
    execution_dataset.parent.mkdir(parents=True)
    execution_dataset.write_text(
        json.dumps(
            {
                "id": "opaque-case",
                "query": "redacted query",
                "intent": "lookup",
                "workspace_fixture": "workspace-a",
                "project_fixture": "project-a",
                "scope": "documents",
                "document_scope": "case",
                "permission_persona": "member",
                "query_type": "prose",
                "language": "en",
                "documents": [
                    {
                        "document_id": "source-a",
                        "filename": "source.txt",
                        "mime_type": "text/plain",
                        "source_type": "document",
                        "content_base64": "ZmFjdA==",
                        "classification": "internal",
                        "revises_document_id": None,
                    }
                ],
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    golden_dataset.write_text(
        json.dumps(
            {
                "id": "opaque-case",
                "answerable": True,
                "expected_facts": ["fact"],
                "expected_source_constraints": [
                    {"document_id": "source-a", "active_version": True}
                ],
                "forbidden_sources": [],
                "adversarial_tags": [],
                "notes": "synthetic receipt contract fixture",
                "reviewer": "synthetic-reviewer",
                "dataset_version": "1.0.0",
                "split": "holdout",
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    independent_evidence = tmp_path / "independent-request-capture.json"
    pack_spec = importlib.util.spec_from_file_location(
        "cv_non_transfer_test_pack", REPO / "scripts/local_benchmark_pack.py"
    )
    assert pack_spec and pack_spec.loader
    pack_module = importlib.util.module_from_spec(pack_spec)
    pack_spec.loader.exec_module(pack_module)
    descriptor = pack_module.bundle_descriptor(pack_root)
    pack = pack_module.LocalBenchmarkPack.open(
        pack_root,
        expected_bundle_sha256=descriptor["sha256"],
        expected_records=1,
        expected_query_types=["prose"],
    )
    pack.execution_projection()
    manifest.update(
        schema_version="3.0",
        dataset_sha256=descriptor["sha256"],
        golden_dataset_sha256=run_eval._sha(golden_dataset),
        execution_dataset_sha256=run_eval._sha(execution_dataset),
        execution_projection_sha256=pack.execution_sha256,
    )
    args.private_pack_manifest.write_text(json.dumps(manifest))
    candidate = {
        **provider_report,
        "golden_results_sent_to_provider": False,
        "schema_version": "2.0",
        "tool_version": run_eval.TOOL_VERSION,
        "tier": "real-benchmark",
        "result": "PASS",
        "observed_at_utc": "2026-09-05T00:00:00+00:00",
        "repository_revision": "f" * 40,
        "dataset_sha256": manifest["dataset_sha256"],
        "dataset_provenance": {
            "kind": "private-manifest-declared",
            "private_manifest_sha256": run_eval._sha(args.private_pack_manifest),
            "review_complete": True,
        },
        "regression_findings": [],
        "release_gate_eligible": False,
        "baseline_review_required": True,
        "quality_claim": "runner-reported-unverified",
        "warnings": [],
        "request_capture_session_sha256": "1" * 64,
        "request_capture_ledger_sha256": "2" * 64,
        "request_capture_request_count": provider_report["usage"]["provider_calls"],
    }
    candidate_path = tmp_path / "candidate-report.json"
    candidate_path.write_text(json.dumps(candidate))
    independent_evidence.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "evidence_type": "local-provider-request-boundary-capture",
                "status": "SEALED_BEFORE_GOLDEN_OPEN",
                "repository_revision": candidate["repository_revision"],
                "collector_sha256": run_eval._sha(
                    REPO / "scripts/capture_local_provider_boundary.py"
                ),
                "runner_bundle_sha256": run_eval._runner_bundle_sha256(
                    args.provider_runner
                ),
                "private_pack_manifest_sha256": run_eval._sha(
                    args.private_pack_manifest
                ),
                "dataset_sha256": descriptor["sha256"],
                "golden_dataset_sha256": run_eval._sha(golden_dataset),
                "execution_dataset_sha256": run_eval._sha(execution_dataset),
                "execution_projection_sha256": pack.execution_sha256,
                "environment_hash": candidate["environment_hash"],
                **{name: candidate[name] for name in run_eval.PROVIDER_MODEL_FIELDS},
                "request_count": candidate["usage"]["provider_calls"],
                "request_kind_counts": {"embedding": 8, "generation": 8},
                "ordered_request_digest": candidate["request_capture_ledger_sha256"],
                "session_sha256": candidate["request_capture_session_sha256"],
                "requests_sealed_at_utc": "2026-09-04T23:58:00Z",
                "golden_first_open_at_utc": "2026-09-04T23:59:00Z",
                "raw_request_retained": False,
                "raw_response_retained": False,
                "golden_payload_retained": False,
            }
        )
    )
    receipt = {
        "schema_version": "1.0",
        "receipt_type": "golden-non-transfer-independent-review",
        "receipt_id": "synthetic-independent-capture-001",
        "decision": "verified-no-golden-transfer",
        "verified_by": "synthetic-independent-reviewer",
        "verified_at_utc": "2026-09-05T01:00:00Z",
        "verification_method": "independent-request-capture",
        "verification_evidence_sha256": run_eval._sha(independent_evidence),
        "verified_request_count": candidate["usage"]["provider_calls"],
        "repository_revision": candidate["repository_revision"],
        "runner_bundle_sha256": run_eval._runner_bundle_sha256(args.provider_runner),
        "private_pack_manifest_sha256": run_eval._sha(args.private_pack_manifest),
        "dataset_sha256": descriptor["sha256"],
        "golden_dataset_sha256": run_eval._sha(golden_dataset),
        "execution_dataset_sha256": run_eval._sha(execution_dataset),
        "execution_projection_sha256": pack.execution_sha256,
        "report_sha256": run_eval._sha(candidate_path),
        "environment_hash": candidate["environment_hash"],
        **{name: candidate[name] for name in run_eval.PROVIDER_MODEL_FIELDS},
    }
    receipt_path = tmp_path / "golden-non-transfer-receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    return (
        args,
        manifest,
        candidate,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        receipt,
        receipt_path,
        runner,
    )


def test_golden_non_transfer_receipt_binds_exact_candidate_without_granting_authority(
    tmp_path, monkeypatch
):
    (
        args,
        _,
        candidate,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        _,
        receipt_path,
        runner,
    ) = golden_non_transfer_fixture(tmp_path, monkeypatch)

    result = run_eval._check_golden_non_transfer_receipt(
        receipt_path,
        candidate_path,
        args.private_pack_manifest,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        args.provider_runner,
    )

    assert result["status"] == "RECEIPT_BOUND_TO_EXACT_CANDIDATE"
    assert result["receipt_binding_verified"] is True
    assert result["receipt_authority_verified"] is False
    assert result["release_gate_eligible"] is False
    assert result["provider_invoked"] is False
    assert result["source_repository_revision"] == candidate["repository_revision"]
    assert result["report_sha256"] == run_eval._sha(candidate_path)
    assert result["golden_results_sent_to_provider"] is False
    assert result["capture_sequence_verified"] is True
    assert result["request_capture_session_sha256"] == "1" * 64
    assert result["request_capture_ledger_sha256"] == "2" * 64
    assert "synthetic-independent-reviewer" not in json.dumps(result)
    assert str(independent_evidence) not in json.dumps(result)
    runner.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("collector_sha256", "9" * 64),
        ("ordered_request_digest", "9" * 64),
        ("session_sha256", "9" * 64),
        ("request_count", 15),
        ("request_kind_counts", {"embedding": 1}),
        ("requests_sealed_at_utc", "2026-09-04T23:59:00Z"),
        ("raw_request_retained", True),
        ("unexpected_raw_prompt", "must-never-project"),
    ],
)
def test_independent_request_capture_evidence_fails_closed(
    tmp_path, monkeypatch, field, value
):
    (
        args,
        _,
        _,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        receipt,
        receipt_path,
        runner,
    ) = golden_non_transfer_fixture(tmp_path, monkeypatch)
    evidence = json.loads(independent_evidence.read_text())
    evidence[field] = value
    independent_evidence.write_text(json.dumps(evidence))
    receipt["verification_evidence_sha256"] = run_eval._sha(independent_evidence)
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._check_golden_non_transfer_receipt(
            receipt_path,
            candidate_path,
            args.private_pack_manifest,
            golden_dataset,
            execution_dataset,
            independent_evidence,
            args.provider_runner,
        )
    runner.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_capture_session_sha256", "9" * 64),
        ("request_capture_ledger_sha256", "9" * 64),
        ("request_capture_request_count", 15),
    ],
)
def test_independent_request_capture_binds_candidate_capture_fields(
    tmp_path, monkeypatch, field, value
):
    (
        args,
        _,
        candidate,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        receipt,
        receipt_path,
        runner,
    ) = golden_non_transfer_fixture(tmp_path, monkeypatch)
    candidate[field] = value
    candidate_path.write_text(json.dumps(candidate))
    receipt["report_sha256"] = run_eval._sha(candidate_path)
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._check_golden_non_transfer_receipt(
            receipt_path,
            candidate_path,
            args.private_pack_manifest,
            golden_dataset,
            execution_dataset,
            independent_evidence,
            args.provider_runner,
        )
    runner.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "2.0"},
        {"decision": "unverified"},
        {"verified_by": "   "},
        {"verified_at_utc": "2999-01-01T00:00:00Z"},
        {"repository_revision": "0" * 40},
        {"runner_bundle_sha256": "0" * 64},
        {"private_pack_manifest_sha256": "0" * 64},
        {"dataset_sha256": "0" * 64},
        {"golden_dataset_sha256": "0" * 64},
        {"execution_dataset_sha256": "0" * 64},
        {"execution_projection_sha256": "0" * 64},
        {"report_sha256": "0" * 64},
        {"environment_hash": "0" * 64},
        {"verified_request_count": 15},
        {"embedding_provider": "other"},
        {"embedding_model": "other"},
        {"generation_provider": "other"},
        {"generation_model": "other"},
        {"unexpected": "self-asserted-authority"},
    ],
)
def test_golden_non_transfer_receipt_drift_fails_closed(tmp_path, monkeypatch, change):
    (
        args,
        _,
        _,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        receipt,
        receipt_path,
        runner,
    ) = golden_non_transfer_fixture(tmp_path, monkeypatch)
    receipt.update(change)
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._check_golden_non_transfer_receipt(
            receipt_path,
            candidate_path,
            args.private_pack_manifest,
            golden_dataset,
            execution_dataset,
            independent_evidence,
            args.provider_runner,
        )
    runner.assert_not_called()


@pytest.mark.parametrize(
    "target",
    [
        "private_manifest",
        "golden_dataset",
        "execution_dataset",
        "independent_evidence",
        "report",
        "runner",
    ],
)
def test_golden_non_transfer_receipt_rejects_bound_file_drift(
    tmp_path, monkeypatch, target
):
    (
        args,
        _,
        _,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        _,
        receipt_path,
        runner,
    ) = golden_non_transfer_fixture(tmp_path, monkeypatch)
    paths = {
        "private_manifest": args.private_pack_manifest,
        "golden_dataset": golden_dataset,
        "execution_dataset": execution_dataset,
        "independent_evidence": independent_evidence,
        "report": candidate_path,
        "runner": Path(args.provider_runner[0]),
    }
    paths[target].write_bytes(paths[target].read_bytes() + b"\n")

    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._check_golden_non_transfer_receipt(
            receipt_path,
            candidate_path,
            args.private_pack_manifest,
            golden_dataset,
            execution_dataset,
            independent_evidence,
            args.provider_runner,
        )
    runner.assert_not_called()


@pytest.mark.parametrize("target", ["receipt", "report", "independent_evidence"])
def test_golden_non_transfer_receipt_rejects_symlink_inputs(
    tmp_path, monkeypatch, target
):
    (
        args,
        _,
        _,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        _,
        receipt_path,
        runner,
    ) = golden_non_transfer_fixture(tmp_path, monkeypatch)
    paths = {
        "receipt": receipt_path,
        "report": candidate_path,
        "independent_evidence": independent_evidence,
    }
    path = paths[target]
    real = path.with_name(path.name + ".real")
    path.replace(real)
    path.symlink_to(real)

    with pytest.raises(run_eval.EnvironmentUnavailable, match="unsafe"):
        run_eval._check_golden_non_transfer_receipt(
            receipt_path,
            candidate_path,
            args.private_pack_manifest,
            golden_dataset,
            execution_dataset,
            independent_evidence,
            args.provider_runner,
        )
    runner.assert_not_called()


def test_golden_non_transfer_receipt_requires_runner_false_assertion(
    tmp_path, monkeypatch
):
    (
        args,
        _,
        candidate,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        receipt,
        receipt_path,
        runner,
    ) = golden_non_transfer_fixture(tmp_path, monkeypatch)
    candidate["golden_results_sent_to_provider"] = None
    candidate_path.write_text(json.dumps(candidate))
    receipt["report_sha256"] = run_eval._sha(candidate_path)
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(run_eval.EnvironmentUnavailable, match="explicit false"):
        run_eval._check_golden_non_transfer_receipt(
            receipt_path,
            candidate_path,
            args.private_pack_manifest,
            golden_dataset,
            execution_dataset,
            independent_evidence,
            args.provider_runner,
        )
    runner.assert_not_called()


def test_golden_non_transfer_receipt_has_no_effect_cli_mode(tmp_path, monkeypatch):
    (
        args,
        _,
        _,
        candidate_path,
        golden_dataset,
        execution_dataset,
        independent_evidence,
        _,
        receipt_path,
        runner,
    ) = golden_non_transfer_fixture(tmp_path, monkeypatch)
    output = tmp_path / "receipt-check.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--check-golden-non-transfer-receipt",
            "--golden-non-transfer-receipt",
            str(receipt_path),
            "--candidate-report",
            str(candidate_path),
            "--private-pack-manifest",
            str(args.private_pack_manifest),
            "--golden-dataset",
            str(golden_dataset),
            "--execution-dataset",
            str(execution_dataset),
            "--non-transfer-evidence",
            str(independent_evidence),
            "--provider-runner",
            args.provider_runner[0],
            "--json-output",
            str(output),
        ],
    )

    assert run_eval.main() == 0
    checked = json.loads(output.read_text())
    assert checked["receipt_binding_verified"] is True
    assert checked["receipt_authority_verified"] is False
    assert checked["release_gate_eligible"] is False
    assert checked["provider_invoked"] is False
    assert output.stat().st_mode & 0o777 == 0o600
    runner.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "wrong"},
        {"dataset_sha256": "not-a-hash"},
        {"classification": "public"},
        {"review_status": "pending"},
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


def test_private_manifest_v2_and_v3_are_version_selected(tmp_path, monkeypatch):
    args, manifest, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    assert (
        run_eval._private_manifest(args.private_pack_manifest)["schema_version"]
        == "2.0"
    )

    listener = enable_v3_request_capture(tmp_path, args, manifest, report)
    try:
        selected = run_eval._private_manifest(args.private_pack_manifest)
    finally:
        close_v3_request_capture(listener, args)
    assert selected["schema_version"] == "3.0"
    assert selected["golden_dataset_sha256"] == "e" * 64


def test_v3_real_benchmark_forwards_only_bounded_capture_environment(
    tmp_path, monkeypatch
):
    args, manifest, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    listener = enable_v3_request_capture(tmp_path, args, manifest, report)
    monkeypatch.setattr(run_eval, "_revision", lambda: "a" * 40)
    try:
        result = run_eval._real_benchmark(args, tmp_path)
    finally:
        close_v3_request_capture(listener, args)

    child_env = runner.call_args.kwargs["env"]
    assert child_env["CV_EVAL_CAPTURE_SOCKET"] == str(args.capture_socket)
    assert child_env["CV_EVAL_CAPTURE_NONCE"] == "4" * 64
    assert child_env["CV_EVAL_GOLDEN_DATASET_SHA256"] == "e" * 64
    assert child_env["CV_EVAL_EXECUTION_DATASET_SHA256"] == "f" * 64
    assert child_env["CV_EVAL_EXECUTION_PROJECTION_SHA256"] == "1" * 64
    assert child_env["CV_EVAL_REPOSITORY_REVISION"] == "a" * 40
    assert str(args.capture_socket) not in json.dumps(result)
    assert str(args.capture_nonce_file) not in json.dumps(result)
    assert "4" * 64 not in json.dumps(result)
    assert result["request_capture_request_count"] == result["usage"]["provider_calls"]


@pytest.mark.parametrize("missing", ["capture_socket", "capture_nonce_file"])
def test_v3_real_benchmark_requires_both_capture_inputs_before_dispatch(
    tmp_path, monkeypatch, missing
):
    args, manifest, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    listener = enable_v3_request_capture(tmp_path, args, manifest, report)
    setattr(args, missing, None)
    try:
        with pytest.raises(run_eval.EnvironmentUnavailable, match="requires capture"):
            run_eval._real_benchmark(args, tmp_path)
    finally:
        close_v3_request_capture(listener, args)
    runner.assert_not_called()


def test_v3_real_benchmark_rejects_non_private_nonce_before_dispatch(
    tmp_path, monkeypatch
):
    args, manifest, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    listener = enable_v3_request_capture(tmp_path, args, manifest, report)
    args.capture_nonce_file.chmod(0o644)
    try:
        with pytest.raises(run_eval.EnvironmentUnavailable, match="mode 0600"):
            run_eval._real_benchmark(args, tmp_path)
    finally:
        close_v3_request_capture(listener, args)
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
        "review_status",
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
        ("schema_version", "1.0"),
        ("embedding_provider", ""),
        ("embedding_model", None),
        ("generation_provider", ""),
        ("generation_model", None),
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


def test_legacy_single_provider_approval_fails_before_dispatch(tmp_path, monkeypatch):
    args, _, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    approval = json.loads(args.approval_manifest.read_text())
    for field in (
        "embedding_provider",
        "embedding_model",
        "generation_provider",
        "generation_model",
    ):
        approval.pop(field)
    approval.update(
        schema_version="1.0",
        provider=report["generation_provider"],
        model=report["generation_model"],
    )
    args.approval_manifest.write_text(json.dumps(approval))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


def test_mixed_legacy_and_v2_approval_fails_before_dispatch(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    approval = json.loads(args.approval_manifest.read_text())
    approval.update(provider="legacy", model="legacy")
    args.approval_manifest.write_text(json.dumps(approval))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


@pytest.mark.parametrize("field", run_eval.PROVIDER_MODEL_FIELDS)
@pytest.mark.parametrize("mutation", ["missing", "blank"])
def test_each_split_approval_identity_is_required_before_dispatch(
    tmp_path, monkeypatch, field, mutation
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    approval = json.loads(args.approval_manifest.read_text())
    if mutation == "missing":
        approval.pop(field)
    else:
        approval[field] = "   "
    args.approval_manifest.write_text(json.dumps(approval))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


def test_legacy_single_provider_report_fails_closed(tmp_path, monkeypatch):
    args, _, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    for field in (
        "embedding_provider",
        "embedding_model",
        "generation_provider",
        "generation_model",
    ):
        report.pop(field)
    report.update(provider="synthetic-test-only", model="not-a-real-model")
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    assert runner.call_count == 1


def test_mixed_legacy_and_v2_report_fails_closed(tmp_path, monkeypatch):
    args, _, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    report.update(provider="legacy", model="legacy")
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    assert runner.call_count == 1


@pytest.mark.parametrize("field", run_eval.PROVIDER_MODEL_FIELDS)
def test_each_split_report_identity_is_required(tmp_path, monkeypatch, field):
    args, _, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    report.pop(field)
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._real_benchmark(args, tmp_path)
    assert runner.call_count == 1


def test_runtime_uses_explicit_final_state_schemas_and_keeps_history():
    approval_v1 = run_eval.APPROVAL_MANIFEST_SCHEMA.with_name(
        "benchmark-approval-manifest-v1.schema.json"
    )
    approval_v2 = run_eval.APPROVAL_MANIFEST_SCHEMA.with_name(
        "benchmark-approval-manifest-v2.schema.json"
    )
    private_v1 = run_eval.PRIVATE_MANIFEST_SCHEMA.with_name(
        "private-pack-manifest.schema.json"
    )
    seal_v1 = run_eval.BASELINE_SEAL_SCHEMA.with_name(
        "benchmark-baseline-seal-v1.schema.json"
    )
    assert run_eval.PRIVATE_MANIFEST_SCHEMA.name.endswith("-v2.schema.json")
    assert run_eval.APPROVAL_MANIFEST_SCHEMA.name.endswith("-v3.schema.json")
    assert run_eval.BASELINE_SEAL_SCHEMA.name.endswith("-v2.schema.json")
    private_schema = json.loads(run_eval.PRIVATE_MANIFEST_SCHEMA.read_text())
    approval_schema = json.loads(run_eval.APPROVAL_MANIFEST_SCHEMA.read_text())
    assert private_schema["$id"].endswith("-v2.json")
    assert private_schema["properties"]["review_status"] == {"const": "approved"}
    assert approval_schema["$id"].endswith("-v3.json")
    assert approval_schema["properties"]["decision"] == {"const": "approved"}
    assert json.loads(run_eval.APPROVAL_MANIFEST_SCHEMA.read_text())["$id"].endswith(
        "-v3.json"
    )
    assert json.loads(run_eval.BASELINE_SEAL_SCHEMA.read_text())["$id"].endswith(
        "-v2.json"
    )
    assert json.loads(approval_v1.read_text())["properties"]["schema_version"] == {
        "const": "1.0"
    }
    assert json.loads(approval_v2.read_text())["properties"]["schema_version"] == {
        "const": "2.0"
    }
    assert json.loads(private_v1.read_text())["properties"]["schema_version"] == {
        "const": "1.0"
    }
    assert json.loads(seal_v1.read_text())["properties"]["schema_version"] == {
        "const": "1.0"
    }


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
    assert child_env["CV_EVAL_EMBEDDING_PROVIDER"] == report["embedding_provider"]
    assert child_env["CV_EVAL_EMBEDDING_MODEL"] == report["embedding_model"]
    assert child_env["CV_EVAL_GENERATION_PROVIDER"] == report["generation_provider"]
    assert child_env["CV_EVAL_GENERATION_MODEL"] == report["generation_model"]
    assert "CV_EVAL_PROVIDER" not in child_env and "CV_EVAL_MODEL" not in child_env
    assert child_env["CV_EVAL_ENVIRONMENT_HASH"] == report["environment_hash"]
    assert child_env["CV_EVAL_DATASET_SHA256"] == manifest["dataset_sha256"]
    assert child_env["CV_EVAL_PRIVATE_PACK_MANIFEST_SHA256"] == run_eval._sha(
        args.private_pack_manifest
    )
    assert child_env["CV_EVAL_PRIVATE_PACK_RECORDS"] == str(manifest["records"])
    assert (
        child_env["CV_EVAL_PRIVATE_PACK_CLASSIFICATION"] == manifest["classification"]
    )
    assert (
        json.loads(child_env["CV_EVAL_PRIVATE_PACK_QUERY_TYPES"])
        == manifest["query_types"]
    )
    assert child_env["CV_EVAL_RUNNER_BUNDLE_SHA256"] == run_eval._runner_bundle_sha256(
        args.provider_runner
    )
    assert child_env["PATH"] == run_eval._runner_path()
    assert child_env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert child_env["PYTHONNOUSERSITE"] == "1"
    assert child_env["PYTHONPYCACHEPREFIX"] == str(tmp_path / "isolated-pycache")
    assert "HOME" not in child_env and "CODEX_SESSION_ID" not in child_env


def test_real_benchmark_normalizes_ambient_path_before_dispatch(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", "/unbound/ambient/path")

    run_eval._real_benchmark(args, tmp_path)

    child_env = runner.call_args.kwargs["env"]
    assert child_env["PATH"] == run_eval._runner_path()
    assert "/unbound/ambient/path" not in child_env["PATH"]


def test_runner_path_selects_current_locked_python_environment(tmp_path):
    entrypoint = tmp_path / "python-runner"
    entrypoint.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        'print(json.dumps({"executable": sys.executable, "prefix": sys.prefix}))\n'
    )
    entrypoint.chmod(0o700)

    completed = subprocess.run(
        [str(entrypoint)],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": run_eval._runner_path(),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
    )
    identity = json.loads(completed.stdout)
    assert Path(identity["executable"]).samefile(Path(sys.executable))
    assert Path(identity["prefix"]).samefile(Path(sys.prefix))
    selected = run_eval._runner_python_identities()["python3"]
    assert Path(selected["path"]).samefile(Path(sys.executable))
    assert selected["sha256"] == run_eval._sha(Path(selected["resolved_path"]))


def test_environment_identity_rejects_runner_python_retarget(tmp_path, monkeypatch):
    unapproved = tmp_path / "python3"
    unapproved.write_text("#!/bin/sh\nexit 0\n")
    unapproved.chmod(0o700)
    real_which = run_eval.shutil.which

    def retargeted_which(command, *, path=None):
        if command == "python3":
            return str(unapproved)
        return real_which(command, path=path)

    monkeypatch.setattr(run_eval.shutil, "which", retargeted_which)
    with pytest.raises(run_eval.EnvironmentUnavailable, match="does not match"):
        run_eval._environment_sha256()


def test_approval_cannot_claim_reserved_runner_environment_name(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    approval = json.loads(args.approval_manifest.read_text())
    approval["credential_env_names"].append("CV_EVAL_DATASET_SHA256")
    args.approval_manifest.write_text(json.dumps(approval))
    monkeypatch.setenv("CV_EVAL_DATASET_SHA256", "0" * 64)

    with pytest.raises(run_eval.EnvironmentUnavailable, match="reserved"):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "LANG",
        "LC_ALL",
        "PYTHONPATH",
        "PYTHONHOME",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "VIRTUAL_ENV",
        "BASH_ENV",
        "HTTP_PROXY",
        "SSL_CERT_FILE",
    ],
)
def test_approval_rejects_execution_control_environment_names(
    tmp_path, monkeypatch, name
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    approval = json.loads(args.approval_manifest.read_text())
    approval["credential_env_names"].append(name)
    args.approval_manifest.write_text(json.dumps(approval))
    monkeypatch.setenv(name, "/attacker/unbound-value")

    with pytest.raises(run_eval.EnvironmentUnavailable, match="execution-control"):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


@pytest.mark.parametrize("mutated", ["runner", "private_manifest", "approval_manifest"])
def test_real_benchmark_rejects_admission_input_drift_during_runner(
    tmp_path, monkeypatch, mutated
):
    args, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)

    def mutating_runner(*_args, **_kwargs):
        (tmp_path / "provider-report.json").write_text(json.dumps(report))
        target = {
            "runner": Path(args.provider_runner[0]),
            "private_manifest": args.private_pack_manifest,
            "approval_manifest": args.approval_manifest,
        }[mutated]
        target.write_text(target.read_text() + "\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_eval.subprocess, "run", mutating_runner)
    with pytest.raises(run_eval.EnvironmentUnavailable, match="changed during"):
        run_eval._real_benchmark(args, tmp_path)


@pytest.mark.parametrize("mutated", ["private_manifest", "approval_manifest"])
def test_approval_check_rechecks_authority_inputs_before_valid_receipt(
    tmp_path, monkeypatch, mutated
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    original_runner_hash = run_eval._runner_bundle_sha256
    calls = 0

    def mutate_on_second_runner_hash(command):
        nonlocal calls
        digest = original_runner_hash(command)
        calls += 1
        if calls == 2:
            target = {
                "private_manifest": args.private_pack_manifest,
                "approval_manifest": args.approval_manifest,
            }[mutated]
            target.write_text(target.read_text() + "\n")
        return digest

    monkeypatch.setattr(run_eval, "_runner_bundle_sha256", mutate_on_second_runner_hash)
    with pytest.raises(run_eval.EnvironmentUnavailable, match="changed during"):
        run_eval._check_approval(
            args.approval_manifest,
            args.private_pack_manifest,
            args.provider_runner,
        )
    runner.assert_not_called()


@pytest.mark.parametrize("mutated", ["private_manifest", "approval_manifest"])
def test_real_benchmark_rechecks_authority_inputs_before_dispatch(
    tmp_path, monkeypatch, mutated
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    original_runner_hash = run_eval._runner_bundle_sha256
    calls = 0

    def mutate_on_second_runner_hash(command):
        nonlocal calls
        digest = original_runner_hash(command)
        calls += 1
        if calls == 2:
            target = {
                "private_manifest": args.private_pack_manifest,
                "approval_manifest": args.approval_manifest,
            }[mutated]
            target.write_text(target.read_text() + "\n")
        return digest

    monkeypatch.setattr(run_eval, "_runner_bundle_sha256", mutate_on_second_runner_hash)
    with pytest.raises(run_eval.EnvironmentUnavailable, match="changed"):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


def test_real_benchmark_rejects_environment_drift_during_runner(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    approved_hash = run_eval._environment_sha256()
    calls = 0

    def changing_environment_hash():
        nonlocal calls
        calls += 1
        return approved_hash if calls < 3 else "0" * 64

    monkeypatch.setattr(run_eval, "_environment_sha256", changing_environment_hash)
    with pytest.raises(run_eval.EnvironmentUnavailable, match="changed"):
        run_eval._real_benchmark(args, tmp_path)
    runner.assert_not_called()


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


def test_provider_report_error_distribution_is_exactly_allowlisted(
    tmp_path, monkeypatch
):
    from src.domain.answer import AnswerValidationCode

    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    assert run_eval.ANSWER_VALIDATION_ERROR_CODES == {
        code.value for code in AnswerValidationCode
    }
    code = AnswerValidationCode.CLAIM_TEXT_NOT_IN_ANSWER.value
    report["metrics"]["error_code_distribution"] = {code: 1}

    assert run_eval._benchmark_report(report)["metrics"]["error_code_distribution"] == {
        code: 1
    }

    report["metrics"]["error_code_distribution"] = {"secret_dynamic_code": 1}
    with pytest.raises(run_eval.EnvironmentUnavailable, match="error distribution"):
        run_eval._benchmark_report(report)

    report["metrics"]["error_code_distribution"] = {code: 2}
    with pytest.raises(run_eval.EnvironmentUnavailable, match="dataset records"):
        run_eval._benchmark_report(report)


@pytest.mark.parametrize(
    "field,value,pre_dispatch",
    [
        ("embedding_provider", "other", False),
        ("embedding_model", "other", False),
        ("generation_provider", "other", False),
        ("generation_model", "other", False),
        ("private_pack_sha256", "0" * 64, True),
        ("dataset_sha256", "0" * 64, True),
        ("allowed_classification", "restricted", True),
        ("runner_bundle_sha256", "0" * 64, True),
        ("environment_hash", "0" * 64, True),
        ("decision", "pending", True),
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
        "schema_version": "2.0",
        "seal_id": "synthetic-seal",
        "sealed_by": "synthetic-human-reviewer",
        "sealed_at_utc": "2026-09-02T00:00:00Z",
        "status": "approved",
        "report_sha256": run_eval._sha(baseline_path),
        **{
            name: baseline[name]
            for name in (
                "embedding_provider",
                "embedding_model",
                "generation_provider",
                "generation_model",
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
    for field in run_eval.PROVIDER_MODEL_FIELDS:
        changed = {**current, field: "different"}
        assert f"{field} differs from approved baseline" in run_eval._compare_with_seal(
            changed, baseline, seal
        )


@pytest.mark.parametrize(
    "change",
    [
        {"status": "rejected"},
        {"report_sha256": "0" * 64},
        {"sealed_by": "   "},
        {"embedding_provider": "other"},
        {"embedding_model": "other"},
        {"generation_provider": "other"},
        {"generation_model": "other"},
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


@pytest.mark.parametrize(
    "change",
    [
        {"status": "pending"},
        {"sealed_by": "   "},
        {"report_sha256": "0" * 64},
        {"sealed_at_utc": "2999-01-01T00:00:00Z"},
    ],
)
def test_main_rejects_invalid_baseline_seal_before_runner_dispatch(
    tmp_path, monkeypatch, change
):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    seal_value = json.loads(seal.read_text())
    seal_value.update(change)
    seal.write_text(json.dumps(seal_value))
    runner_effect = Mock()
    monkeypatch.setattr(run_eval, "_real_benchmark", runner_effect)
    output = tmp_path / "must-not-exist.json"
    markdown = tmp_path / "must-not-exist.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--tier",
            "real-benchmark",
            "--baseline",
            str(baseline),
            "--baseline-seal",
            str(seal),
            "--json-output",
            str(output),
            "--markdown-output",
            str(markdown),
        ],
    )

    assert run_eval.main() == 3
    runner_effect.assert_not_called()
    assert not output.exists()
    assert not markdown.exists()


def test_main_rejects_unpaired_baseline_before_runner_dispatch(tmp_path, monkeypatch):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, _seal = sealed_baseline(tmp_path, report)
    runner_effect = Mock()
    monkeypatch.setattr(run_eval, "_real_benchmark", runner_effect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--tier",
            "real-benchmark",
            "--baseline",
            str(baseline),
            "--json-output",
            str(tmp_path / "must-not-exist.json"),
            "--markdown-output",
            str(tmp_path / "must-not-exist.md"),
        ],
    )

    assert run_eval.main() == 3
    runner_effect.assert_not_called()


def test_main_projects_exact_prevalidated_baseline_hashes(tmp_path, monkeypatch):
    _, manifest, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    runner_effect = Mock(
        return_value={
            **report,
            "result": "PASS",
            "dataset_sha256": manifest["dataset_sha256"],
        }
    )
    monkeypatch.setattr(run_eval, "_real_benchmark", runner_effect)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    output = tmp_path / "current.json"
    markdown = tmp_path / "current.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--tier",
            "real-benchmark",
            "--baseline",
            str(baseline),
            "--baseline-seal",
            str(seal),
            "--json-output",
            str(output),
            "--markdown-output",
            str(markdown),
        ],
    )

    assert run_eval.main() == 0
    current = json.loads(output.read_text())
    assert current["baseline_report_sha256"] == run_eval._sha(baseline)
    assert current["baseline_seal_sha256"] == run_eval._sha(seal)
    assert str(baseline) not in output.read_text()
    assert str(seal) not in output.read_text()
    runner_effect.assert_called_once()


def test_legacy_single_provider_baseline_seal_fails_closed(tmp_path, monkeypatch):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    value = json.loads(seal.read_text())
    for field in (
        "embedding_provider",
        "embedding_model",
        "generation_provider",
        "generation_model",
    ):
        value.pop(field)
    value.update(
        schema_version="1.0",
        provider=report["generation_provider"],
        model=report["generation_model"],
    )
    seal.write_text(json.dumps(value))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._compare_with_seal(report, baseline, seal)


def test_legacy_v1_baseline_report_fails_closed(tmp_path, monkeypatch):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    baseline_value = json.loads(baseline.read_text())
    baseline_value["schema_version"] = "1.0"
    baseline.write_text(json.dumps(baseline_value))
    seal_value = json.loads(seal.read_text())
    seal_value["report_sha256"] = run_eval._sha(baseline)
    seal.write_text(json.dumps(seal_value))
    with pytest.raises(run_eval.EnvironmentUnavailable, match="successful"):
        run_eval._compare_with_seal(report, baseline, seal)


def test_mixed_legacy_and_v2_baseline_report_fails_closed(tmp_path, monkeypatch):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    baseline_value = json.loads(baseline.read_text())
    baseline_value.update(provider="legacy", model="legacy")
    baseline.write_text(json.dumps(baseline_value))
    seal_value = json.loads(seal.read_text())
    seal_value["report_sha256"] = run_eval._sha(baseline)
    seal.write_text(json.dumps(seal_value))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._compare_with_seal(report, baseline, seal)


def test_sealed_baseline_requires_full_v2_report_contract(tmp_path, monkeypatch):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    baseline_value = json.loads(baseline.read_text())
    baseline_value["metrics"].pop("recall@1")
    baseline.write_text(json.dumps(baseline_value))
    seal_value = json.loads(seal.read_text())
    seal_value["report_sha256"] = run_eval._sha(baseline)
    seal.write_text(json.dumps(seal_value))
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._compare_with_seal(report, baseline, seal)


def test_current_comparison_candidate_requires_full_v2_report_contract(
    tmp_path, monkeypatch
):
    _, _, report, _ = benchmark_fixture(tmp_path, monkeypatch)
    baseline, seal = sealed_baseline(tmp_path, report)
    current = {
        **report,
        "schema_version": "1.0",
        "dataset_sha256": "a" * 64,
    }
    with pytest.raises(run_eval.EnvironmentUnavailable):
        run_eval._compare_with_seal(current, baseline, seal)


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


def test_approval_preflight_is_bounded_deterministic_and_has_no_effect(
    tmp_path, monkeypatch
):
    args, manifest, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    manifest["secret_store_reference"] = "PRIVATE-SECRET-REFERENCE"
    args.private_pack_manifest.write_text(json.dumps(manifest))
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)

    first = run_eval._approval_preflight(
        args.private_pack_manifest, args.provider_runner
    )
    second = run_eval._approval_preflight(
        args.private_pack_manifest, args.provider_runner
    )

    assert first == second
    assert set(first) == {
        "schema_version",
        "request_type",
        "status",
        "repository_revision",
        "tool_version",
        "private_pack_sha256",
        "dataset_sha256",
        "allowed_classification",
        "runner_bundle_sha256",
        "environment_hash",
        "provider_invoked",
        "credential_values_read",
        "human_decisions_required",
    }
    assert first["status"] == "HUMAN_APPROVAL_REQUIRED"
    assert first["schema_version"] == "3.0"
    assert all(
        name in first["human_decisions_required"]
        for name in run_eval.PROVIDER_MODEL_FIELDS
    )
    assert "provider" not in first["human_decisions_required"]
    assert "model" not in first["human_decisions_required"]
    assert first["provider_invoked"] is False
    assert first["credential_values_read"] is False
    output = json.dumps(first)
    assert "PRIVATE-SECRET-REFERENCE" not in output
    assert str(args.private_pack_manifest) not in output
    assert str(args.provider_runner[0]) not in output
    assert manifest["reviewed_by"] not in output
    runner.assert_not_called()


def test_approval_preflight_fingerprints_manifest_runner_and_environment(
    tmp_path, monkeypatch
):
    args, manifest, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    first = run_eval._approval_preflight(
        args.private_pack_manifest, args.provider_runner
    )

    manifest["records"] = 2
    args.private_pack_manifest.write_text(json.dumps(manifest))
    manifest_changed = run_eval._approval_preflight(
        args.private_pack_manifest, args.provider_runner
    )
    assert manifest_changed["private_pack_sha256"] != first["private_pack_sha256"]

    Path(args.provider_runner[0]).write_text("#!/bin/sh\nexit 98\n")
    runner_changed = run_eval._approval_preflight(
        args.private_pack_manifest, args.provider_runner
    )
    assert runner_changed["runner_bundle_sha256"] != first["runner_bundle_sha256"]

    monkeypatch.setattr(run_eval.platform, "machine", lambda: "changed-machine")
    environment_changed = run_eval._approval_preflight(
        args.private_pack_manifest, args.provider_runner
    )
    assert environment_changed["environment_hash"] != first["environment_hash"]
    runner.assert_not_called()


def test_approval_preflight_rejects_symlink_and_oversized_private_manifest(
    tmp_path, monkeypatch
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    manifest_link = tmp_path / "private-manifest-link.json"
    manifest_link.symlink_to(args.private_pack_manifest)

    with pytest.raises(run_eval.EnvironmentUnavailable, match="unsafe"):
        run_eval._approval_preflight(manifest_link, args.provider_runner)

    oversized = tmp_path / "oversized-private-manifest.json"
    oversized.write_bytes(b"{" + b" " * 1_000_000 + b"}")
    with pytest.raises(run_eval.EnvironmentUnavailable, match="bounded regular"):
        run_eval._approval_preflight(oversized, args.provider_runner)
    runner.assert_not_called()


def test_approval_preflight_rechecks_manifest_before_return(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    original_runner_hash = run_eval._runner_bundle_sha256

    def mutate_during_preflight(command):
        digest = original_runner_hash(command)
        args.private_pack_manifest.write_text(
            args.private_pack_manifest.read_text() + "\n"
        )
        return digest

    monkeypatch.setattr(run_eval, "_runner_bundle_sha256", mutate_during_preflight)
    with pytest.raises(run_eval.EnvironmentUnavailable, match="changed during"):
        run_eval._approval_preflight(args.private_pack_manifest, args.provider_runner)
    runner.assert_not_called()


def test_approval_check_rejects_symlinked_authority_inputs(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    manifest_link = tmp_path / "private-manifest-link.json"
    approval_link = tmp_path / "approval-link.json"
    manifest_link.symlink_to(args.private_pack_manifest)
    approval_link.symlink_to(args.approval_manifest)

    with pytest.raises(run_eval.EnvironmentUnavailable, match="unsafe"):
        run_eval._check_approval(
            args.approval_manifest,
            manifest_link,
            args.provider_runner,
        )
    with pytest.raises(run_eval.EnvironmentUnavailable, match="unsafe"):
        run_eval._check_approval(
            approval_link,
            args.private_pack_manifest,
            args.provider_runner,
        )
    runner.assert_not_called()


def test_check_approval_validates_bindings_without_runner_or_credentials(
    tmp_path, monkeypatch
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    monkeypatch.delenv("SYNTHETIC_PROVIDER_KEY")

    result = run_eval._check_approval(
        args.approval_manifest,
        args.private_pack_manifest,
        args.provider_runner,
    )

    assert result["status"] == "APPROVAL_VALID_FOR_CURRENT_INPUTS"
    assert result["schema_version"] == "3.0"
    assert all(result[name] for name in run_eval.PROVIDER_MODEL_FIELDS)
    assert result["provider_invoked"] is False
    assert result["credential_values_read"] is False
    assert "SYNTHETIC_PROVIDER_KEY" not in json.dumps(result)
    runner.assert_not_called()


@pytest.mark.parametrize(
    "reviewer",
    ["Pendington Security", "Unapprovedly Named Reviewer"],
)
def test_explicit_final_state_does_not_infer_status_from_actor_name(
    tmp_path, monkeypatch, reviewer
):
    args, manifest, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    manifest["reviewed_by"] = reviewer
    args.private_pack_manifest.write_text(json.dumps(manifest))
    approval = json.loads(args.approval_manifest.read_text())
    approval["approved_by"] = reviewer
    approval["private_pack_sha256"] = run_eval._sha(args.private_pack_manifest)
    args.approval_manifest.write_text(json.dumps(approval))

    checked = run_eval._check_approval(
        args.approval_manifest,
        args.private_pack_manifest,
        args.provider_runner,
    )

    assert checked["status"] == "APPROVAL_VALID_FOR_CURRENT_INPUTS"
    runner.assert_not_called()


def test_main_keeps_v2_for_real_report_and_v1_for_nonreal_tiers(tmp_path, monkeypatch):
    args, manifest, report, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    monkeypatch.setattr(
        run_eval,
        "_real_benchmark",
        lambda *_args: {
            **report,
            "result": "PASS",
            "dataset_sha256": manifest["dataset_sha256"],
        },
    )
    output = tmp_path / "real.json"
    markdown = tmp_path / "real.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--tier",
            "real-benchmark",
            "--approval-manifest",
            str(args.approval_manifest),
            "--private-pack-manifest",
            str(args.private_pack_manifest),
            "--provider-runner",
            args.provider_runner[0],
            "--json-output",
            str(output),
            "--markdown-output",
            str(markdown),
        ],
    )
    assert run_eval.main() == 0
    assert json.loads(output.read_text())["schema_version"] == "2.0"
    runner.assert_not_called()


def test_check_approval_rejects_private_binding_drift_without_effect(
    tmp_path, monkeypatch
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    approval = json.loads(args.approval_manifest.read_text())
    approval["dataset_sha256"] = "0" * 64
    args.approval_manifest.write_text(json.dumps(approval))

    with pytest.raises(run_eval.EnvironmentUnavailable, match="binding mismatch"):
        run_eval._check_approval(
            args.approval_manifest,
            args.private_pack_manifest,
            args.provider_runner,
        )
    runner.assert_not_called()


def test_private_pack_future_review_is_rejected_before_preflight(tmp_path, monkeypatch):
    args, manifest, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    manifest["approved_at_utc"] = "2999-01-01T00:00:00Z"
    args.private_pack_manifest.write_text(json.dumps(manifest))

    with pytest.raises(run_eval.EnvironmentUnavailable, match="future"):
        run_eval._approval_preflight(args.private_pack_manifest, args.provider_runner)
    runner.assert_not_called()


def test_approval_preflight_and_check_are_supported_no_effect_cli_modes(
    tmp_path, monkeypatch
):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(run_eval, "_revision", lambda: "f" * 40)
    preflight_output = tmp_path / "preflight.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--approval-preflight",
            "--private-pack-manifest",
            str(args.private_pack_manifest),
            "--provider-runner",
            args.provider_runner[0],
            "--json-output",
            str(preflight_output),
        ],
    )
    assert run_eval.main() == 0
    assert json.loads(preflight_output.read_text())["status"] == (
        "HUMAN_APPROVAL_REQUIRED"
    )

    monkeypatch.delenv("SYNTHETIC_PROVIDER_KEY")
    check_output = tmp_path / "approval-check.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--check-approval",
            "--approval-manifest",
            str(args.approval_manifest),
            "--private-pack-manifest",
            str(args.private_pack_manifest),
            "--provider-runner",
            args.provider_runner[0],
            "--json-output",
            str(check_output),
        ],
    )
    assert run_eval.main() == 0
    checked = json.loads(check_output.read_text())
    assert checked["status"] == "APPROVAL_VALID_FOR_CURRENT_INPUTS"
    assert checked["credential_values_read"] is False
    assert preflight_output.stat().st_mode & 0o777 == 0o600
    runner.assert_not_called()


def test_approval_preparation_never_overwrites_an_existing_file(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    original = args.private_pack_manifest.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--approval-preflight",
            "--private-pack-manifest",
            str(args.private_pack_manifest),
            "--provider-runner",
            args.provider_runner[0],
            "--json-output",
            str(args.private_pack_manifest),
        ],
    )

    assert run_eval.main() == 3
    assert args.private_pack_manifest.read_bytes() == original
    runner.assert_not_called()


def test_approval_preparation_output_must_remain_outside_repository():
    repository_output = REPO / ".approval-preflight-must-not-be-created.json"

    with pytest.raises(run_eval.EnvironmentUnavailable, match="outside"):
        run_eval._validate_approval_output_path(repository_output)
    assert not repository_output.exists()


def test_approval_preflight_rejects_non_executable_runner(tmp_path, monkeypatch):
    args, _, _, runner = benchmark_fixture(tmp_path, monkeypatch)
    Path(args.provider_runner[0]).chmod(0o600)

    with pytest.raises(run_eval.EnvironmentUnavailable, match="executable"):
        run_eval._approval_preflight(args.private_pack_manifest, args.provider_runner)
    runner.assert_not_called()


def test_approval_output_rejects_dangling_symlink_without_creating_target(tmp_path):
    target = tmp_path / "must-not-be-created.json"
    output = tmp_path / "dangling-output.json"
    output.symlink_to(target)

    with pytest.raises(run_eval.EnvironmentUnavailable, match="new file"):
        run_eval._write_approval_output(output, {"bounded": True})
    assert output.is_symlink()
    assert not target.exists()


def test_approval_preflight_failure_envelope_is_not_a_null_eval_tier(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--approval-preflight",
            "--json-output",
            str(tmp_path / "not-created.json"),
        ],
    )

    assert run_eval.main() == 3
    failure = json.loads(capsys.readouterr().out)
    assert failure["request_type"] == "real-benchmark-approval-preflight"
    assert failure["provider_invoked"] is False
    assert "tier" not in failure
