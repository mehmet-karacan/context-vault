#!/usr/bin/env python3
"""Shared, secret-safe execution core for repository verification CLIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


TOOL_VERSION = "1.0.0"
REPORT_SCHEMA = "context-vault-verifier-report/v1"
EXIT_PASS = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
BACKEND = Path("document-rag-platform/services/backend")
FRONTEND = Path("document-rag-platform/apps/web")


class ConfigurationError(RuntimeError):
    """The verifier invocation or repository layout is invalid."""


class EnvironmentUnavailable(RuntimeError):
    """A required executable or runtime dependency is unavailable."""


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    criterion: str
    kind: str
    arguments: tuple[str, ...]
    cwd: Path
    warning_on_pass: str | None = None


@dataclass(frozen=True)
class VerifierSpec:
    name: str
    responsibility: str
    checks: tuple[CheckSpec, ...]
    integration_checks: tuple[CheckSpec, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


CommandRunner = Callable[[Sequence[str], Path, int], CommandResult]
Clock = Callable[[], datetime]


def _pytest(check_id: str, criterion: str, *paths: str) -> CheckSpec:
    return CheckSpec(
        check_id=check_id,
        criterion=criterion,
        kind="pytest",
        arguments=paths,
        cwd=BACKEND,
    )


def _npm(check_id: str, criterion: str, script: str) -> CheckSpec:
    return CheckSpec(
        check_id=check_id,
        criterion=criterion,
        kind="npm",
        arguments=(script,),
        cwd=FRONTEND,
    )


SPECS: dict[str, VerifierSpec] = {
    "verify_data_integrity": VerifierSpec(
        name="verify_data_integrity",
        responsibility="version/chunk/profile/artifact/outbox/orphan integrity",
        checks=(
            _pytest(
                "data.unit-contracts",
                "version_chunk_profile_artifact_outbox_orphan_integrity",
                "tests/test_db_admission.py",
                "tests/test_domain_edge_contracts.py",
                "tests/test_v3_data_migration.py",
            ),
        ),
        integration_checks=(
            _pytest(
                "data.database-invariants",
                "live_database_schema_and_data_invariants",
                "tests/integration/test_database_contract.py",
                "tests/integration/test_schema_invariants.py",
            ),
        ),
    ),
    "verify_public_safety": VerifierSpec(
        name="verify_public_safety",
        responsibility="public secret, endpoint, certificate, dump and log safety",
        checks=(
            CheckSpec(
                "public.actual-tree-scan",
                "tracked_tree_contains_no_forbidden_public_material",
                "json-command",
                (
                    "{python}",
                    "scripts/check_public_tree.py",
                    "--repo",
                    "{repo}",
                    "--json-output",
                    "{json_output}",
                ),
                Path("."),
            ),
            _pytest(
                "public.scanner-regressions",
                "public_scanner_redaction_and_path_safety",
                "tests/test_public_tree_scan.py",
                "tests/test_path_security.py",
                "tests/test_redaction.py",
            ),
        ),
    ),
    "verify_api_scope": VerifierSpec(
        name="verify_api_scope",
        responsibility="authentication and workspace/project/conversation isolation",
        checks=(
            _pytest(
                "scope.unit-contracts",
                "auth_workspace_project_conversation_scope",
                "tests/test_auth_scope.py",
                "tests/test_repositories_api.py",
                "tests/test_main_app.py",
            ),
        ),
        integration_checks=(
            _pytest(
                "scope.database-leakage",
                "cross_workspace_enumeration_is_denied",
                "tests/integration/test_identity_scope.py",
            ),
        ),
    ),
    "verify_ingestion": VerifierSpec(
        name="verify_ingestion",
        responsibility="ingestion E2E, idempotency, outbox, retry and policy",
        checks=(
            _pytest(
                "ingestion.unit-contracts",
                "one_orchestrator_idempotency_retry_and_policy",
                "tests/test_ingestion_architecture.py",
                "tests/test_ingestion_bundle.py",
                "tests/test_ingestion_tasks.py",
                "tests/test_file_validation.py",
            ),
        ),
        integration_checks=(
            _pytest(
                "ingestion.external-e2e",
                "database_object_store_ingestion_e2e",
                "tests/integration/test_ingestion_control_plane.py",
                "tests/integration/test_ingestion_minio_e2e.py",
            ),
        ),
    ),
    "verify_retrieval": VerifierSpec(
        name="verify_retrieval",
        responsibility="active profile, RRF, context budget and index plans",
        checks=(
            _pytest(
                "retrieval.unit-contracts",
                "active_profile_rrf_context_budget_and_scope",
                "tests/test_retrieval_service.py",
                "tests/test_rrf.py",
                "tests/test_context_builder.py",
                "tests/test_dense.py",
                "tests/test_lexical.py",
            ),
        ),
        integration_checks=(
            _pytest(
                "retrieval.database-plans",
                "typed_retrieval_and_database_index_plan_eligibility",
                "tests/integration/test_typed_retrieval.py",
            ),
        ),
    ),
    "verify_citations": VerifierSpec(
        name="verify_citations",
        responsibility="citation labels, claims, evidence hash and provenance",
        checks=(
            _pytest(
                "citations.unit-contracts",
                "labels_claims_evidence_hash_and_provenance",
                "tests/test_answer_service.py",
                "tests/test_chat_citations.py",
                "tests/test_structured_answer.py",
                "tests/test_no_answer.py",
            ),
        ),
        integration_checks=(
            _pytest(
                "citations.persistence",
                "structured_answer_and_citation_persistence",
                "tests/integration/test_structured_answer_persistence.py",
            ),
        ),
    ),
    "verify_frontend": VerifierSpec(
        name="verify_frontend",
        responsibility="generated client, lint, type, unit, build, E2E and a11y",
        checks=(
            _npm(
                "frontend.api-client",
                "generated_api_client_has_no_drift",
                "api-client-check",
            ),
            _npm("frontend.lint", "frontend_lint", "lint"),
            _npm("frontend.typecheck", "frontend_typecheck", "typecheck"),
            _npm("frontend.unit", "frontend_unit_tests", "unit"),
            _npm("frontend.build", "frontend_production_build", "build"),
        ),
        integration_checks=(
            _npm("frontend.e2e-a11y", "frontend_e2e_and_accessibility", "e2e-smoke"),
        ),
    ),
    "verify_runtime": VerifierSpec(
        name="verify_runtime",
        responsibility="compose, health/readiness, logging, tracing and orphans",
        checks=(
            _pytest(
                "runtime.contracts",
                "compose_health_readiness_observability_and_orphan_contracts",
                "tests/test_deployment_contract.py",
                "tests/test_health.py",
                "tests/test_observability.py",
                "tests/test_ops_doctor.py",
                "tests/test_slo_rehearsal.py",
                "tests/test_backup_scheduler.py",
            ),
        ),
    ),
    "verify_restore": VerifierSpec(
        name="verify_restore",
        responsibility="database/object restore and reconciliation",
        checks=(
            _pytest(
                "restore.contracts",
                "backup_restore_reconciliation_contracts",
                "tests/test_backup_restore_ops.py",
            ),
            CheckSpec(
                "restore.dry-run",
                "restore_plan_is_admitted_without_effects",
                "json-command",
                (
                    "{python}",
                    "scripts/backup_restore.py",
                    "--repo",
                    "{repo}",
                    "--dry-run",
                    "--json-output",
                    "{json_output}",
                ),
                Path("."),
                warning_on_pass="a dry-run is not evidence of a completed restore drill",
            ),
        ),
    ),
    "verify_context_protocol": VerifierSpec(
        name="verify_context_protocol",
        responsibility="claim, fencing, receipt, compiler and adapter conformance",
        checks=(
            _pytest(
                "context.protocol-contracts",
                "claim_fencing_receipt_compiler_and_adapter_conformance",
                "tests/test_work_graph_state.py",
                "tests/test_work_graph_effects.py",
                "tests/test_context_compiler.py",
                "tests/test_adapters.py",
                "tests/test_cv_cli.py",
                "tests/test_handoff.py",
                "tests/test_registry.py",
                "tests/test_knowledge_lifecycle.py",
            ),
        ),
        integration_checks=(
            _pytest(
                "context.persistence",
                "work_graph_and_context_vault_persistence",
                "tests/integration/test_work_graph_persistence.py",
                "tests/integration/test_context_vault_persistence.py",
            ),
        ),
    ),
}


LEGACY_RELEASE_EVIDENCE = (
    "generate_verified_status",
    "run_eval",
    "run_eval_non_transfer",
    "verify_baseline",
    "verify_migrations",
)
RELEASE_COMPONENTS = tuple(sorted(SPECS))
REQUIRED_RELEASE_EVIDENCE = tuple(
    sorted((*RELEASE_COMPONENTS, *LEGACY_RELEASE_EVIDENCE))
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repository_head(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise EnvironmentUnavailable("git repository revision is unavailable") from exc
    head = result.stdout.strip()
    if len(head) != 40 or any(
        character not in "0123456789abcdef" for character in head
    ):
        raise EnvironmentUnavailable("git returned an invalid repository revision")
    return head


def repository_dirty(repo: Path) -> tuple[bool, int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise EnvironmentUnavailable("git working-tree state is unavailable") from exc
    entries = [item for item in result.stdout.split(b"\0") if item]
    return bool(entries), len(entries), sha256_bytes(result.stdout)


def validate_expected_sha(value: str) -> None:
    if len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ConfigurationError("--expected-sha must be a full lowercase commit SHA")


def default_command_runner(
    arguments: Sequence[str], cwd: Path, timeout: int
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(arguments),
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EnvironmentUnavailable(
            "required command executable is unavailable"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise EnvironmentUnavailable("verification command timed out") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _junit_summary(path: Path) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise EnvironmentUnavailable(
            "pytest did not produce readable JUnit evidence"
        ) from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.attrib.get(key, "0"))
    totals["passed"] = max(
        0,
        totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"],
    )
    return totals


def _safe_child_projection(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"valid_json_object": False}
    allowed = (
        "schema_version",
        "status",
        "scope",
        "tracked_files_total",
        "tracked_files_checked",
        "effects_performed",
        "credential_values_read",
    )
    projection = {key: payload[key] for key in allowed if key in payload}
    findings = payload.get("findings")
    if isinstance(findings, list):
        projection["finding_count"] = len(findings)
    return projection


def _execute_check(
    check: CheckSpec,
    repo: Path,
    timeout: int,
    runner: CommandRunner,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    cwd = repo / check.cwd
    if not cwd.is_dir():
        raise ConfigurationError(
            f"required repository directory is missing: {check.cwd}"
        )
    findings: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="context-vault-verifier-") as temporary:
        temporary_path = Path(temporary)
        junit_path = temporary_path / "junit.xml"
        child_json = temporary_path / "child.json"
        if check.kind == "pytest":
            arguments = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *check.arguments,
                f"--junitxml={junit_path}",
            ]
        elif check.kind == "npm":
            executable = "npm.cmd" if os.name == "nt" else "npm"
            if shutil.which(executable) is None:
                raise EnvironmentUnavailable("npm executable is unavailable")
            if not (repo / FRONTEND / "node_modules").is_dir():
                raise EnvironmentUnavailable("frontend dependencies are not installed")
            arguments = [executable, "run", check.arguments[0], "--silent"]
        elif check.kind == "json-command":
            replacements = {
                "{python}": sys.executable,
                "{repo}": str(repo),
                "{json_output}": str(child_json),
            }
            arguments = [replacements.get(value, value) for value in check.arguments]
        else:
            raise ConfigurationError(f"unknown check kind: {check.kind}")

        result = runner(arguments, cwd, timeout)
        if check.kind == "json-command" and result.returncode == EXIT_USAGE:
            raise ConfigurationError(
                "authoritative child verifier rejected its configuration"
            )
        if check.kind == "json-command" and result.returncode == EXIT_ENVIRONMENT:
            raise EnvironmentUnavailable(
                "authoritative child verifier environment is unavailable"
            )
        if check.kind == "pytest" and result.returncode in {4, 5}:
            raise ConfigurationError("configured pytest selection is invalid or empty")
        output_digest = sha256_bytes(result.stdout + b"\0" + result.stderr)
        evidence: dict[str, Any] = {
            "exit_code": result.returncode,
            "output_sha256": output_digest,
        }
        status = "PASS" if result.returncode == 0 else "FAIL"

        if check.kind == "pytest" and junit_path.is_file():
            summary = _junit_summary(junit_path)
            evidence["tests"] = summary
            if summary["skipped"] and status == "PASS":
                status = "WARN"
                findings.append(
                    {
                        "criterion": check.criterion,
                        "severity": "warning",
                        "message": f"{summary['skipped']} test(s) were skipped",
                    }
                )
        elif check.kind == "json-command":
            if not child_json.is_file():
                status = "FAIL"
                evidence["child_report"] = {"present": False}
            else:
                try:
                    payload = json.loads(child_json.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    status = "FAIL"
                    evidence["child_report"] = {"valid_json_object": False}
                else:
                    evidence["child_report"] = _safe_child_projection(payload)
                    child_status = (
                        payload.get("status") if isinstance(payload, dict) else None
                    )
                    if child_status not in {"PASS", "DRY_RUN"}:
                        status = "FAIL"

        if result.returncode != 0:
            findings.append(
                {
                    "criterion": check.criterion,
                    "severity": "error",
                    "message": "the authoritative verification command failed",
                }
            )
        if check.warning_on_pass and status == "PASS":
            status = "WARN"
            findings.append(
                {
                    "criterion": check.criterion,
                    "severity": "warning",
                    "message": check.warning_on_pass,
                }
            )
        return (
            {
                "check_id": check.check_id,
                "criterion": check.criterion,
                "status": status,
                "evidence": evidence,
            },
            findings,
        )


def _runtime_versions(spec: VerifierSpec) -> dict[str, str]:
    versions = {"python": platform.python_version()}
    if any(check.kind == "pytest" for check in spec.checks + spec.integration_checks):
        try:
            from importlib.metadata import version

            versions["pytest"] = version("pytest")
        except Exception as exc:
            raise EnvironmentUnavailable("pytest package is unavailable") from exc
    return dict(sorted(versions.items()))


def _restore_receipt_check(
    path: Path, repo: Path, expected_sha: str
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    candidate = path if path.is_absolute() else repo / path
    errors: list[str] = []
    payload: dict[str, Any] | None = None
    if not candidate.is_file():
        errors.append("restore receipt is missing")
    else:
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            errors.append("restore receipt is not valid UTF-8 JSON")
        else:
            if not isinstance(loaded, dict):
                errors.append("restore receipt root is not an object")
            else:
                payload = loaded
    if payload is not None:
        expected_values = {
            "schema_version": "1.0",
            "receipt_type": "fresh-target-restore-drill",
            "status": "PASS",
            "source_repository_revision": expected_sha,
            "executor_repository_revision": expected_sha,
            "fresh_target_verified": True,
            "source_mutated": False,
            "raw_object_names_retained": False,
            "credential_values_retained": False,
        }
        for key, value in expected_values.items():
            if payload.get(key) != value:
                errors.append(f"restore receipt field {key} is invalid")
        smokes = payload.get("smoke")
        if smokes != {
            "migration": True,
            "auth": True,
            "retrieval": True,
            "citation": True,
        }:
            errors.append("restore receipt smoke gates are incomplete")
        reconciliation = payload.get("reconciliation")
        if not isinstance(reconciliation, dict) or any(
            reconciliation.get(key) != 0
            for key in ("missing_object_count", "orphan_object_count")
        ):
            errors.append("restore receipt reconciliation did not converge")
        version = payload.get("tool_version")
        if not isinstance(version, str) or not version:
            errors.append("restore receipt tool version is missing")
    evidence = {
        "present": candidate.is_file(),
        "sha256": sha256_file(candidate) if candidate.is_file() else None,
        "error_count": len(errors),
    }
    findings = [
        {
            "criterion": "completed_fresh_target_restore",
            "severity": "error",
            "message": message,
        }
        for message in errors
    ]
    return (
        {
            "check_id": "restore.completed-drill",
            "criterion": "completed_fresh_target_restore",
            "status": "FAIL" if errors else "PASS",
            "evidence": evidence,
        },
        findings,
    )


def verify_spec(
    spec: VerifierSpec,
    *,
    repo: Path,
    expected_sha: str | None,
    strict: bool,
    include_integration: bool,
    timeout: int,
    restore_receipt: Path | None = None,
    runner: CommandRunner = default_command_runner,
    clock: Clock = utc_now,
) -> tuple[dict[str, Any], int]:
    repo = repo.resolve()
    if timeout < 1:
        raise ConfigurationError("timeout must be a positive integer")
    head = repository_head(repo)
    dirty, dirty_count, dirty_digest = repository_dirty(repo)
    expected = expected_sha or head
    validate_expected_sha(expected)
    findings: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []
    if head != expected:
        findings.append(
            {
                "criterion": "exact_candidate_sha",
                "severity": "error",
                "message": "repository HEAD does not match --expected-sha",
            }
        )
    if dirty:
        findings.append(
            {
                "criterion": "clean_candidate_tree",
                "severity": "error",
                "message": "repository working tree is dirty",
            }
        )

    selected = list(spec.checks)
    if spec.name == "verify_restore" and restore_receipt is not None:
        selected = [replace(item, warning_on_pass=None) for item in selected]
    if include_integration:
        selected.extend(spec.integration_checks)
    elif spec.integration_checks:
        findings.append(
            {
                "criterion": "integration_evidence",
                "severity": "warning",
                "message": "integration checks were not requested",
            }
        )
        checks.extend(
            {
                "check_id": item.check_id,
                "criterion": item.criterion,
                "status": "NOT_RUN",
                "evidence": {"reason": "use --include-integration"},
            }
            for item in spec.integration_checks
        )

    for check in selected:
        check_report, check_findings = _execute_check(check, repo, timeout, runner)
        checks.append(check_report)
        findings.extend(check_findings)

    if spec.name == "verify_restore" and restore_receipt is not None:
        receipt_check, receipt_findings = _restore_receipt_check(
            restore_receipt, repo, expected
        )
        checks.append(receipt_check)
        findings.extend(receipt_findings)

    checks.sort(key=lambda item: item["check_id"])
    findings.sort(
        key=lambda item: (item["criterion"], item["severity"], item["message"])
    )
    has_error = any(item["severity"] == "error" for item in findings)
    has_warning = any(item["severity"] == "warning" for item in findings)
    failed = has_error or (strict and has_warning)
    report = {
        "schema": REPORT_SCHEMA,
        "tool": spec.name,
        "tool_version": TOOL_VERSION,
        "observed_at_utc": utc_text(clock()),
        "repository_head": head,
        "repository_state": {
            "dirty": dirty,
            "dirty_entry_count": dirty_count,
            "porcelain_sha256": dirty_digest,
        },
        "expected_sha": expected,
        "strict": strict,
        "responsibility": spec.responsibility,
        "runtime_versions": _runtime_versions(spec),
        "checks": checks,
        "findings": findings,
        "result": "FAIL" if failed else "PASS",
    }
    return report, EXIT_VALIDATION if failed else EXIT_PASS


def _release_paths(
    repo: Path, reports_dir: Path | None, evidence: Sequence[Path]
) -> dict[str, Path]:
    if evidence:
        paths: dict[str, Path] = {}
        for path in evidence:
            candidate = path if path.is_absolute() else repo / path
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConfigurationError(
                    "release evidence is not readable JSON"
                ) from exc
            tool = _release_evidence_identity(payload)
            if not isinstance(tool, str) or tool in paths:
                raise ConfigurationError(
                    "release evidence tool identity is invalid or duplicated"
                )
            paths[tool] = candidate
        return paths
    directory = reports_dir or Path("artifacts/release-input")
    directory = directory if directory.is_absolute() else repo / directory
    names = {
        "generate_verified_status": "verified-status.json",
        "run_eval": "run_eval.json",
        "run_eval_non_transfer": "run_eval-non-transfer.json",
        "verify_baseline": "verify_baseline.json",
        "verify_migrations": "verify_migrations.json",
    }
    return {
        name: directory / names.get(name, f"{name}.json")
        for name in REQUIRED_RELEASE_EVIDENCE
    }


def _release_evidence_identity(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool")
    if tool in RELEASE_COMPONENTS:
        return str(tool)
    schema = payload.get("schema")
    if schema == "context-vault-baseline-verification/v1":
        return "verify_baseline"
    if schema == "context-vault-migration-verification/v1":
        return "verify_migrations"
    if (
        payload.get("schema_version") == 1
        and "verified" in payload
        and "head_sha" in payload
    ):
        return "generate_verified_status"
    if "tier" in payload and "repository_revision" in payload:
        return "run_eval"
    if payload.get("request_type") == "golden-non-transfer-receipt-check":
        return "run_eval_non_transfer"
    return None


def _legacy_evidence_errors(
    identity: str, payload: dict[str, Any], expected_sha: str
) -> list[str]:
    errors: list[str] = []
    if identity == "verify_baseline":
        repository = payload.get("repository")
        if not isinstance(repository, dict):
            errors.append("baseline repository evidence is missing")
        else:
            if repository.get("head_sha") != expected_sha:
                errors.append("baseline is not bound to exact candidate SHA")
            if repository.get("dirty") is not False:
                errors.append("baseline candidate tree is not clean")
        if payload.get("result") != "PASS":
            errors.append("baseline result is not PASS")
        if not isinstance(payload.get("tool_version"), str):
            errors.append("baseline tool version is missing")
    elif identity == "verify_migrations":
        if payload.get("repository_head") != expected_sha:
            errors.append("migration evidence is not bound to exact candidate SHA")
        if payload.get("result") != "PASS":
            errors.append("migration result is not PASS")
        if not isinstance(payload.get("tool_version"), str):
            errors.append("migration tool version is missing")
        if not isinstance(payload.get("database"), dict):
            errors.append("migration evidence does not contain a database verification")
    elif identity == "generate_verified_status":
        if payload.get("head_sha") != expected_sha:
            errors.append("verified status is not bound to exact candidate SHA")
        if payload.get("verified") is not True:
            errors.append("machine status is not verified")
        if payload.get("dirty_paths") != []:
            errors.append("machine status candidate tree is not clean")
    elif identity == "run_eval":
        if payload.get("repository_revision") != expected_sha:
            errors.append("evaluation is not bound to exact candidate SHA")
        if payload.get("tier") != "real-benchmark":
            errors.append("evaluation is not the approved real-benchmark tier")
        if payload.get("result") != "PASS":
            errors.append("evaluation result is not PASS")
        if payload.get("baseline_review_required") is not False:
            errors.append("evaluation does not use a human-sealed baseline")
        if payload.get("regression_candidate_eligible") is not True:
            errors.append("evaluation is not an approved regression candidate")
        # A runner must never promote its own report. The independent aggregate
        # verifier can accept the candidate only together with the separately
        # issued and checked no-golden-transfer receipt below.
        if payload.get("release_gate_eligible") is not False:
            errors.append("evaluation runner improperly granted release authority")
        if payload.get("golden_results_sent_to_provider") is not False:
            errors.append("evaluation lacks the explicit no-golden-transfer assertion")
        for field in ("baseline_report_sha256", "baseline_seal_sha256"):
            value = payload.get(field)
            if not isinstance(value, str) or len(value) != 64:
                errors.append(f"evaluation {field} is missing")
        if payload.get("regression_findings") != []:
            errors.append("evaluation contains regression findings")
        if not isinstance(payload.get("tool_version"), str):
            errors.append("evaluation tool version is missing")
    elif identity == "run_eval_non_transfer":
        if payload.get("source_repository_revision") != expected_sha:
            errors.append("non-transfer evidence is not bound to exact candidate SHA")
        if payload.get("status") != "RECEIPT_BOUND_TO_EXACT_CANDIDATE":
            errors.append("non-transfer receipt status is invalid")
        if payload.get("receipt_binding_verified") is not True:
            errors.append("non-transfer receipt binding is not verified")
        if payload.get("golden_results_sent_to_provider") is not False:
            errors.append("non-transfer receipt does not prove zero golden transfer")
        if payload.get("provider_invoked") is not False:
            errors.append(
                "non-transfer receipt checker unexpectedly invoked a provider"
            )
        if payload.get("receipt_authority_verified") is not False:
            errors.append("non-transfer checker improperly claimed reviewer authority")
        if payload.get("release_gate_eligible") is not False:
            errors.append("non-transfer checker improperly granted release authority")
        if payload.get("human_release_decision_required") is not True:
            errors.append("non-transfer evidence omits the human release decision gate")
        if not isinstance(payload.get("tool_version"), str):
            errors.append("non-transfer checker tool version is missing")
    return errors


def verify_release(
    *,
    repo: Path,
    expected_sha: str | None,
    strict: bool,
    reports_dir: Path | None,
    evidence: Sequence[Path],
    clock: Clock = utc_now,
) -> tuple[dict[str, Any], int]:
    repo = repo.resolve()
    head = repository_head(repo)
    dirty, dirty_count, dirty_digest = repository_dirty(repo)
    expected = expected_sha or head
    validate_expected_sha(expected)
    findings: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    if head != expected:
        findings.append(
            {
                "criterion": "exact_candidate_sha",
                "severity": "error",
                "message": "repository HEAD does not match --expected-sha",
            }
        )
    if dirty:
        findings.append(
            {
                "criterion": "clean_candidate_tree",
                "severity": "error",
                "message": "repository working tree is dirty",
            }
        )
    paths = _release_paths(repo, reports_dir, evidence)
    for name in REQUIRED_RELEASE_EVIDENCE:
        path = paths.get(name)
        errors: list[str] = []
        payload: dict[str, Any] | None = None
        if path is None or not path.is_file():
            errors.append("required verifier report is missing")
        else:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                errors.append("verifier report is not valid UTF-8 JSON")
            else:
                if not isinstance(loaded, dict):
                    errors.append("verifier report root is not an object")
                else:
                    payload = loaded
        if payload is not None and name in RELEASE_COMPONENTS:
            if payload.get("schema") != REPORT_SCHEMA:
                errors.append("verifier report schema is invalid")
            if payload.get("tool") != name:
                errors.append("verifier tool identity is invalid")
            if payload.get("repository_head") != expected:
                errors.append("verifier report is not bound to exact candidate SHA")
            version = payload.get("tool_version")
            if not isinstance(version, str) or not version:
                errors.append("verifier tool version is missing")
            if payload.get("result") != "PASS":
                errors.append("verifier result is not PASS")
            state = payload.get("repository_state")
            if not isinstance(state, dict) or state.get("dirty") is not False:
                errors.append("verifier candidate tree is not clean")
            if strict and payload.get("strict") is not True:
                errors.append("strict release requires a strict verifier report")
        elif payload is not None:
            errors.extend(_legacy_evidence_errors(name, payload, expected))
        if payload is not None:
            assert path is not None
            artifacts.append(
                {
                    "tool": name,
                    "path": path.relative_to(repo).as_posix()
                    if path.is_relative_to(repo)
                    else "<external>",
                    "sha256": sha256_file(path),
                }
            )
        if errors:
            findings.extend(
                {
                    "criterion": f"release_component_{name}",
                    "severity": "error",
                    "message": message,
                }
                for message in errors
            )
        checks.append(
            {
                "check_id": f"release.{name}",
                "criterion": f"release_component_{name}",
                "status": "FAIL" if errors else "PASS",
                "evidence": {"error_count": len(errors)},
            }
        )

    checks.sort(key=lambda item: item["check_id"])
    artifacts.sort(key=lambda item: item["tool"])
    findings.sort(
        key=lambda item: (item["criterion"], item["severity"], item["message"])
    )
    failed = bool(findings)
    report = {
        "schema": REPORT_SCHEMA,
        "tool": "verify_release",
        "tool_version": TOOL_VERSION,
        "observed_at_utc": utc_text(clock()),
        "repository_head": head,
        "repository_state": {
            "dirty": dirty,
            "dirty_entry_count": dirty_count,
            "porcelain_sha256": dirty_digest,
        },
        "expected_sha": expected,
        "strict": strict,
        "responsibility": "aggregate exact-SHA verifier evidence into a release manifest",
        "runtime_versions": {"python": platform.python_version()},
        "checks": checks,
        "artifacts": artifacts,
        "findings": findings,
        "result": "FAIL" if failed else "PASS",
    }
    return report, EXIT_VALIDATION if failed else EXIT_PASS


def parser_for(tool: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Run {tool} repository verification.")
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--expected-sha")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--strict", action="store_true")
    if tool == "verify_release":
        parser.add_argument("--reports-dir", type=Path)
        parser.add_argument("--evidence", type=Path, action="append", default=[])
    else:
        parser.add_argument("--include-integration", action="store_true")
        parser.add_argument("--timeout", type=int, default=900)
        if tool == "verify_restore":
            parser.add_argument("--restore-receipt", type=Path)
    return parser


def _error_report(tool: str, result: str, message: str, clock: Clock) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "tool": tool,
        "tool_version": TOOL_VERSION,
        "observed_at_utc": utc_text(clock()),
        "result": result,
        "error": message,
    }


def emit_report(report: dict[str, Any], output: Path | None) -> None:
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)


def run_named_cli(tool: str, argv: list[str] | None = None) -> int:
    args = parser_for(tool).parse_args(argv)
    try:
        if tool == "verify_release":
            report, exit_code = verify_release(
                repo=args.repo,
                expected_sha=args.expected_sha,
                strict=args.strict,
                reports_dir=args.reports_dir,
                evidence=args.evidence,
            )
        else:
            spec = SPECS.get(tool)
            if spec is None:
                raise ConfigurationError("unknown verifier")
            report, exit_code = verify_spec(
                spec,
                repo=args.repo,
                expected_sha=args.expected_sha,
                strict=args.strict,
                include_integration=args.include_integration,
                timeout=args.timeout,
                restore_receipt=getattr(args, "restore_receipt", None),
            )
    except ConfigurationError as exc:
        report = _error_report(tool, "CONFIG_ERROR", str(exc), utc_now)
        exit_code = EXIT_USAGE
    except EnvironmentUnavailable as exc:
        report = _error_report(tool, "ENVIRONMENT_UNAVAILABLE", str(exc), utc_now)
        exit_code = EXIT_ENVIRONMENT
    try:
        emit_report(report, args.json_output)
    except OSError:
        fallback = _error_report(
            tool, "CONFIG_ERROR", "JSON output path is not writable", utc_now
        )
        sys.stdout.write(json.dumps(fallback, sort_keys=True) + "\n")
        return EXIT_USAGE
    return exit_code
