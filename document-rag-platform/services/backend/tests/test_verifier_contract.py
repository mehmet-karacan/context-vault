"""Regression coverage for the shared A13 verifier CLI contract."""

from __future__ import annotations

import hashlib

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verifier_core  # noqa: E402


HEAD = "a" * 40
FIXED_TIME = datetime(2026, 9, 6, 12, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_repository(monkeypatch) -> None:
    monkeypatch.setattr(
        verifier_core,
        "repository_dirty",
        lambda _repo: (False, 0, verifier_core.sha256_bytes(b"")),
    )


def _repo(tmp_path: Path) -> Path:
    backend = tmp_path / verifier_core.BACKEND
    backend.mkdir(parents=True)
    return tmp_path


def _passing_pytest(arguments, _cwd, _timeout):
    junit = next(
        value.split("=", 1)[1] for value in arguments if value.startswith("--junitxml=")
    )
    Path(junit).write_text(
        '<testsuites tests="2" failures="0" errors="0" skipped="0"/>',
        encoding="utf-8",
    )
    return verifier_core.CommandResult(0, b"two passed", b"")


def _single_check_spec(*, integration: bool = False) -> verifier_core.VerifierSpec:
    check = verifier_core.CheckSpec(
        "example.check",
        "acceptance_example",
        "pytest",
        ("tests/test_example.py",),
        verifier_core.BACKEND,
    )
    return verifier_core.VerifierSpec(
        "verify_example",
        "example responsibility",
        checks=(check,),
        integration_checks=(check,) if integration else (),
    )


def test_shared_report_is_exact_sha_utc_sorted_and_secret_safe(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(verifier_core, "repository_head", lambda _repo: HEAD)

    def runner(arguments, cwd, timeout):
        result = _passing_pytest(arguments, cwd, timeout)
        return verifier_core.CommandResult(
            result.returncode,
            b"api_key=super-secret-value",
            b"password=another-secret-value",
        )

    report, exit_code = verifier_core.verify_spec(
        _single_check_spec(),
        repo=repo,
        expected_sha=HEAD,
        strict=True,
        include_integration=False,
        timeout=10,
        runner=runner,
        clock=lambda: FIXED_TIME,
    )

    assert exit_code == verifier_core.EXIT_PASS
    assert report["repository_head"] == HEAD
    assert report["tool_version"] == verifier_core.TOOL_VERSION
    assert report["observed_at_utc"] == "2026-09-06T12:30:00Z"
    serialized = json.dumps(report, sort_keys=True)
    assert "super-secret-value" not in serialized
    assert "another-secret-value" not in serialized
    assert len(report["checks"][0]["evidence"]["output_sha256"]) == 64


def test_warning_passes_normally_and_fails_strict(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(verifier_core, "repository_head", lambda _repo: HEAD)
    spec = _single_check_spec(integration=True)

    normal, normal_exit = verifier_core.verify_spec(
        spec,
        repo=repo,
        expected_sha=HEAD,
        strict=False,
        include_integration=False,
        timeout=10,
        runner=_passing_pytest,
        clock=lambda: FIXED_TIME,
    )
    strict, strict_exit = verifier_core.verify_spec(
        spec,
        repo=repo,
        expected_sha=HEAD,
        strict=True,
        include_integration=False,
        timeout=10,
        runner=_passing_pytest,
        clock=lambda: FIXED_TIME,
    )

    assert normal["result"] == "PASS"
    assert normal_exit == verifier_core.EXIT_PASS
    assert strict["result"] == "FAIL"
    assert strict_exit == verifier_core.EXIT_VALIDATION
    assert any(item["status"] == "NOT_RUN" for item in strict["checks"])


def test_failed_authoritative_command_closes_named_criterion(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(verifier_core, "repository_head", lambda _repo: HEAD)

    def failing(arguments, cwd, timeout):
        result = _passing_pytest(arguments, cwd, timeout)
        return verifier_core.CommandResult(1, result.stdout, b"failure details")

    report, exit_code = verifier_core.verify_spec(
        _single_check_spec(),
        repo=repo,
        expected_sha=HEAD,
        strict=False,
        include_integration=False,
        timeout=10,
        runner=failing,
        clock=lambda: FIXED_TIME,
    )

    assert exit_code == verifier_core.EXIT_VALIDATION
    assert report["result"] == "FAIL"
    assert report["checks"][0]["status"] == "FAIL"
    assert report["findings"] == [
        {
            "criterion": "acceptance_example",
            "severity": "error",
            "message": "the authoritative verification command failed",
        }
    ]


def test_exact_sha_mismatch_is_validation_failure(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(verifier_core, "repository_head", lambda _repo: HEAD)
    report, exit_code = verifier_core.verify_spec(
        _single_check_spec(),
        repo=repo,
        expected_sha="b" * 40,
        strict=False,
        include_integration=False,
        timeout=10,
        runner=_passing_pytest,
        clock=lambda: FIXED_TIME,
    )
    assert exit_code == verifier_core.EXIT_VALIDATION
    assert report["result"] == "FAIL"
    assert report["findings"][0]["criterion"] == "exact_candidate_sha"


def test_malformed_expected_sha_is_configuration_error(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(verifier_core, "repository_head", lambda _repo: HEAD)
    try:
        verifier_core.verify_spec(
            _single_check_spec(),
            repo=repo,
            expected_sha="main",
            strict=False,
            include_integration=False,
            timeout=10,
            runner=_passing_pytest,
            clock=lambda: FIXED_TIME,
        )
    except verifier_core.ConfigurationError as error:
        assert "full lowercase commit SHA" in str(error)
    else:
        raise AssertionError("malformed SHA must be rejected as configuration")


def test_release_requires_every_exact_sha_pass_report(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path
    evidence_dir = repo / "reports"
    evidence_dir.mkdir()
    monkeypatch.setattr(verifier_core, "repository_head", lambda _repo: HEAD)
    for name in verifier_core.RELEASE_COMPONENTS:
        (evidence_dir / f"{name}.json").write_text(
            json.dumps(
                {
                    "schema": verifier_core.REPORT_SCHEMA,
                    "tool": name,
                    "tool_version": verifier_core.TOOL_VERSION,
                    "repository_head": HEAD,
                    "repository_state": {"dirty": False},
                    "strict": True,
                    "result": "PASS",
                }
            ),
            encoding="utf-8",
        )
    legacy = {
        "verify_baseline.json": {
            "schema": "context-vault-baseline-verification/v1",
            "tool_version": "1.1.0",
            "repository": {"head_sha": HEAD, "dirty": False},
            "result": "PASS",
        },
        "verify_migrations.json": {
            "schema": "context-vault-migration-verification/v1",
            "tool_version": "1.2.0",
            "repository_head": HEAD,
            "database": {"revision": "cv3_00000007"},
            "result": "PASS",
        },
        "verified-status.json": {
            "schema_version": 1,
            "head_sha": HEAD,
            "verified": True,
            "dirty_paths": [],
        },
        "run_eval.json": {
            "schema_version": "2.0",
            "tool_version": "3.0.0",
            "tier": "real-benchmark",
            "repository_revision": HEAD,
            "result": "PASS",
            "baseline_review_required": False,
            "regression_candidate_eligible": True,
            "release_gate_eligible": False,
            "golden_results_sent_to_provider": False,
            "baseline_report_sha256": "a" * 64,
            "baseline_seal_sha256": "b" * 64,
            "regression_findings": [],
        },
        "run_eval-non-transfer.json": {
            "schema_version": "1.0",
            "request_type": "golden-non-transfer-receipt-check",
            "status": "RECEIPT_BOUND_TO_EXACT_CANDIDATE",
            "source_repository_revision": HEAD,
            "checker_repository_revision": HEAD,
            "tool_version": "3.0.0",
            "receipt_binding_verified": True,
            "golden_results_sent_to_provider": False,
            "provider_invoked": False,
            "receipt_authority_verified": False,
            "release_gate_eligible": False,
            "human_release_decision_required": True,
        },
    }
    run_eval_bytes = json.dumps(legacy["run_eval.json"])
    legacy["run_eval-non-transfer.json"]["report_sha256"] = hashlib.sha256(
        run_eval_bytes.encode()
    ).hexdigest()
    for filename, payload in legacy.items():
        content = run_eval_bytes if filename == "run_eval.json" else json.dumps(payload)
        (evidence_dir / filename).write_text(content, encoding="utf-8")

    report, exit_code = verifier_core.verify_release(
        repo=repo,
        expected_sha=HEAD,
        strict=True,
        reports_dir=evidence_dir,
        evidence=[],
        clock=lambda: FIXED_TIME,
    )
    assert exit_code == verifier_core.EXIT_PASS
    assert report["result"] == "PASS"
    assert [item["tool"] for item in report["artifacts"]] == list(
        verifier_core.REQUIRED_RELEASE_EVIDENCE
    )

    broken = evidence_dir / f"{verifier_core.RELEASE_COMPONENTS[0]}.json"
    payload = json.loads(broken.read_text(encoding="utf-8"))
    payload["repository_head"] = "b" * 40
    broken.write_text(json.dumps(payload), encoding="utf-8")
    report, exit_code = verifier_core.verify_release(
        repo=repo,
        expected_sha=HEAD,
        strict=False,
        reports_dir=evidence_dir,
        evidence=[],
        clock=lambda: FIXED_TIME,
    )
    assert exit_code == verifier_core.EXIT_VALIDATION
    assert report["result"] == "FAIL"
    assert any("exact candidate SHA" in item["message"] for item in report["findings"])


def test_release_promotes_only_independently_reviewable_eval() -> None:
    candidate = {
        "tool_version": "3.0.0",
        "tier": "real-benchmark",
        "repository_revision": HEAD,
        "result": "PASS",
        "baseline_review_required": False,
        "regression_candidate_eligible": True,
        "release_gate_eligible": False,
        "golden_results_sent_to_provider": False,
        "baseline_report_sha256": "a" * 64,
        "baseline_seal_sha256": "b" * 64,
        "regression_findings": [],
    }
    assert verifier_core._legacy_evidence_errors("run_eval", candidate, HEAD) == []

    candidate["release_gate_eligible"] = True
    errors = verifier_core._legacy_evidence_errors("run_eval", candidate, HEAD)
    assert "evaluation runner improperly granted release authority" in errors
    candidate["release_gate_eligible"] = False
    candidate["baseline_report_sha256"] = "z" * 64
    errors = verifier_core._legacy_evidence_errors("run_eval", candidate, HEAD)
    assert "evaluation baseline_report_sha256 is missing or invalid" in errors
    candidate["baseline_report_sha256"] = "a" * 64

    non_transfer = {
        "tool_version": "3.0.0",
        "checker_repository_revision": HEAD,
        "source_repository_revision": HEAD,
        "report_sha256": "c" * 64,
        "status": "RECEIPT_BOUND_TO_EXACT_CANDIDATE",
        "receipt_binding_verified": True,
        "golden_results_sent_to_provider": False,
        "provider_invoked": False,
        "receipt_authority_verified": False,
        "release_gate_eligible": False,
        "human_release_decision_required": True,
    }
    assert (
        verifier_core._legacy_evidence_errors(
            "run_eval_non_transfer", non_transfer, HEAD
        )
        == []
    )

    non_transfer["receipt_binding_verified"] = False
    errors = verifier_core._legacy_evidence_errors(
        "run_eval_non_transfer", non_transfer, HEAD
    )
    assert "non-transfer receipt binding is not verified" in errors
    non_transfer["receipt_binding_verified"] = True
    del non_transfer["checker_repository_revision"]
    del non_transfer["report_sha256"]
    errors = verifier_core._legacy_evidence_errors(
        "run_eval_non_transfer", non_transfer, HEAD
    )
    assert "non-transfer checker is not the exact candidate version" in errors
    assert "non-transfer receipt report hash is missing or invalid" in errors


def test_release_rejects_non_transfer_receipt_not_bound_to_run_eval_bytes(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path
    evidence_dir = repo / "reports"
    evidence_dir.mkdir()
    monkeypatch.setattr(verifier_core, "repository_head", lambda _repo: HEAD)
    monkeypatch.setattr(
        verifier_core,
        "repository_dirty",
        lambda _repo: (False, 0, hashlib.sha256(b"").hexdigest()),
    )

    for name in verifier_core.RELEASE_COMPONENTS:
        (evidence_dir / f"{name}.json").write_text(
            json.dumps(
                {
                    "schema": verifier_core.REPORT_SCHEMA,
                    "tool": name,
                    "tool_version": verifier_core.TOOL_VERSION,
                    "repository_head": HEAD,
                    "repository_state": {"dirty": False},
                    "strict": True,
                    "result": "PASS",
                }
            ),
            encoding="utf-8",
        )

    run_eval = {
        "schema_version": "2.0",
        "tool_version": "3.0.0",
        "tier": "real-benchmark",
        "repository_revision": HEAD,
        "result": "PASS",
        "baseline_review_required": False,
        "regression_candidate_eligible": True,
        "release_gate_eligible": False,
        "golden_results_sent_to_provider": False,
        "baseline_report_sha256": "a" * 64,
        "baseline_seal_sha256": "b" * 64,
        "regression_findings": [],
    }
    legacy = {
        "verify_baseline.json": {
            "schema": "context-vault-baseline-verification/v1",
            "tool_version": "1.1.0",
            "repository": {"head_sha": HEAD, "dirty": False},
            "result": "PASS",
        },
        "verify_migrations.json": {
            "schema": "context-vault-migration-verification/v1",
            "tool_version": "1.2.0",
            "repository_head": HEAD,
            "database": {"revision": "cv3_00000007"},
            "result": "PASS",
        },
        "verified-status.json": {
            "schema_version": 1,
            "head_sha": HEAD,
            "verified": True,
            "dirty_paths": [],
        },
        "run_eval.json": run_eval,
        "run_eval-non-transfer.json": {
            "schema_version": "1.0",
            "request_type": "golden-non-transfer-receipt-check",
            "status": "RECEIPT_BOUND_TO_EXACT_CANDIDATE",
            "checker_repository_revision": HEAD,
            "source_repository_revision": HEAD,
            "tool_version": "3.0.0",
            "report_sha256": "0" * 64,
            "receipt_binding_verified": True,
            "golden_results_sent_to_provider": False,
            "provider_invoked": False,
            "receipt_authority_verified": False,
            "release_gate_eligible": False,
            "human_release_decision_required": True,
        },
    }
    for filename, payload in legacy.items():
        (evidence_dir / filename).write_text(json.dumps(payload), encoding="utf-8")

    report, exit_code = verifier_core.verify_release(
        repo=repo,
        expected_sha=HEAD,
        strict=True,
        reports_dir=evidence_dir,
        evidence=[],
        clock=lambda: FIXED_TIME,
    )
    assert exit_code == verifier_core.EXIT_VALIDATION
    assert report["result"] == "FAIL"
    assert any(
        item["message"]
        == "non-transfer receipt is not bound to the exact run_eval bytes"
        for item in report["findings"]
    )


def test_dirty_tree_fails_closed_without_exposing_paths(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(verifier_core, "repository_head", lambda _repo: HEAD)
    monkeypatch.setattr(
        verifier_core,
        "repository_dirty",
        lambda _repo: (True, 2, "f" * 64),
    )
    report, exit_code = verifier_core.verify_spec(
        _single_check_spec(),
        repo=repo,
        expected_sha=HEAD,
        strict=False,
        include_integration=False,
        timeout=10,
        runner=_passing_pytest,
        clock=lambda: FIXED_TIME,
    )
    assert exit_code == verifier_core.EXIT_VALIDATION
    assert report["repository_state"] == {
        "dirty": True,
        "dirty_entry_count": 2,
        "porcelain_sha256": "f" * 64,
    }
    assert "dirty_paths" not in json.dumps(report)


def test_restore_receipt_must_be_complete_and_exact_sha(tmp_path: Path) -> None:
    receipt = tmp_path / "restore.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "receipt_type": "fresh-target-restore-drill",
                "tool_version": "2.0.0",
                "status": "PASS",
                "source_repository_revision": HEAD,
                "executor_repository_revision": HEAD,
                "fresh_target_verified": True,
                "source_mutated": False,
                "raw_object_names_retained": False,
                "credential_values_retained": False,
                "smoke": {
                    "migration": True,
                    "auth": True,
                    "retrieval": True,
                    "citation": True,
                },
                "reconciliation": {
                    "missing_object_count": 0,
                    "orphan_object_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    check, findings = verifier_core._restore_receipt_check(receipt, tmp_path, HEAD)
    assert check["status"] == "PASS"
    assert findings == []

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["smoke"]["citation"] = False
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    check, findings = verifier_core._restore_receipt_check(receipt, tmp_path, HEAD)
    assert check["status"] == "FAIL"
    assert findings[0]["criterion"] == "completed_fresh_target_restore"


def test_all_required_thin_clis_exist_and_use_shared_core() -> None:
    required = (*verifier_core.RELEASE_COMPONENTS, "verify_release")
    for name in required:
        content = (SCRIPTS / f"{name}.py").read_text(encoding="utf-8")
        assert "from verifier_core import run_named_cli" in content
        assert f'run_named_cli("{name}")' in content


def test_standard_exit_codes_are_stable() -> None:
    assert (
        verifier_core.EXIT_PASS,
        verifier_core.EXIT_VALIDATION,
        verifier_core.EXIT_USAGE,
        verifier_core.EXIT_ENVIRONMENT,
    ) == (0, 1, 2, 3)
