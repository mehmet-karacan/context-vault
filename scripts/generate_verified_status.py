#!/usr/bin/env python3
"""Generate a fail-closed, exact-checkout Context Vault status projection."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO / "status" / "verified-state.json"
MIGRATIONS = REPO / "document-rag-platform/services/backend/alembic/versions_v3"
EVAL_REPORT = (
    REPO
    / "document-rag-platform/services/backend/tests/evals/results/metrics-report.json"
)
REQUIRED_EVIDENCE = (
    "document-rag-platform/artifacts/audit/2026-09-02-baseline/audit-manifest.json",
    "document-rag-platform/artifacts/migrations/2026-09-02-v3-lineage/MIGRATION_RECEIPT.json",
    "document-rag-platform/artifacts/migrations/2026-09-02-v3-lineage/DATA_MIGRATION_RECEIPT.json",
)
REMOTE_EVIDENCE = (
    "document-rag-platform/artifacts/ci/latest/ci-runs.json",
    "document-rag-platform/artifacts/governance/latest/main-ruleset.json",
)
HISTORICAL_PROJECTIONS = (
    "context-summary.md",
    "active/current-tasks.md",
    "IMPLEMENTATION_CHECKLIST.md",
    "done/completed-tasks.md",
)
PROJECTION_SHA = re.compile(r"\*\*last_verified_sha:\*\*\s*`([0-9a-f]{40})`")
REPOSITORY_ID = "mehmet-karacan/context-vault"
REQUIRED_CI_WORKFLOWS = {
    "ci-backend",
    "ci-frontend",
    "ci-rag-contract",
    "ci-security",
}
REQUIRED_STATUS_CHECKS = REQUIRED_CI_WORKFLOWS | {"commit-ownership"}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.rstrip("\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def literal(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)
    raise ValueError(f"{path}: missing {name}")


def migration_heads() -> list[str]:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted(MIGRATIONS.glob("*.py")):
        revision = literal(path, "revision")
        down_revision = literal(path, "down_revision")
        revisions.add(revision)
        if isinstance(down_revision, str):
            parents.add(down_revision)
        elif down_revision:
            parents.update(down_revision)
    return sorted(revisions - parents)


def indexed_files(index_path: Path, pattern: str) -> tuple[list[str], list[str]]:
    files = sorted(
        path.name for path in index_path.parent.glob(pattern) if path != index_path
    )
    index = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    missing = [name for name in files if name not in index]
    return files, missing


def inspect_historical_projection(content: str, *, head: str) -> dict[str, Any]:
    required_labels = (
        "historical/non-canonical",
        "last_verified_sha",
        "last_verified_at",
        "evidence_manifest",
    )
    missing_labels = [label for label in required_labels if label not in content]
    match = PROJECTION_SHA.search(content)
    recorded_sha = match.group(1) if match else None
    is_stale = recorded_sha != head
    stale_warning_present = "STALE UYARISI" in content
    safe = (
        not missing_labels
        and recorded_sha is not None
        and (not is_stale or stale_warning_present)
    )
    return {
        "recorded_sha": recorded_sha,
        "is_stale": is_stale,
        "stale_warning_present": stale_warning_present,
        "missing_labels": missing_labels,
        "safe": safe,
    }


def _remote_receipt(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    result: dict[str, Any] = {
        "present": path.is_file(),
        "sha256": sha256(path) if path.is_file() else None,
        "valid": False,
        "errors": [],
    }
    if not path.is_file():
        result["errors"].append("missing receipt")
        return None, result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        result["errors"].append("receipt is not valid UTF-8 JSON")
        return None, result
    if not isinstance(payload, dict):
        result["errors"].append("receipt root must be an object")
        return None, result
    return payload, result


def inspect_remote_ci_receipt(path: Path, *, head: str) -> dict[str, Any]:
    payload, result = _remote_receipt(path)
    if payload is None:
        return result
    expected = {
        "schema_version": "1.0",
        "repository": REPOSITORY_ID,
        "head_sha": head,
        "status": "PASS",
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            result["errors"].append(f"{field} must equal {value!r}")

    runs = payload.get("runs")
    successful: set[str] = set()
    if not isinstance(runs, list):
        result["errors"].append("runs must be a list")
    else:
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                result["errors"].append(f"runs[{index}] must be an object")
                continue
            name = run.get("workflow")
            if name not in REQUIRED_CI_WORKFLOWS:
                continue
            if run.get("head_sha") != head:
                result["errors"].append(f"{name} is not bound to exact HEAD")
            if run.get("conclusion") != "success":
                result["errors"].append(f"{name} conclusion is not success")
            if run.get("event") not in {"push", "pull_request"}:
                result["errors"].append(f"{name} event is not automatic")
            if not isinstance(run.get("run_id"), int) or run["run_id"] <= 0:
                result["errors"].append(f"{name} run_id is invalid")
            run_url = run.get("run_url")
            if not isinstance(run_url, str) or not run_url.startswith(
                "https://github.com/mehmet-karacan/context-vault/actions/runs/"
            ):
                result["errors"].append(f"{name} run_url is invalid")
            if not any(error.startswith(name) for error in result["errors"]):
                successful.add(name)
    missing = sorted(REQUIRED_CI_WORKFLOWS - successful)
    if missing:
        result["errors"].append(f"missing successful workflows: {', '.join(missing)}")
    result["valid"] = not result["errors"]
    result["successful_workflows"] = sorted(successful)
    return result


def inspect_main_ruleset_receipt(path: Path, *, head: str) -> dict[str, Any]:
    payload, result = _remote_receipt(path)
    if payload is None:
        return result
    expected = {
        "schema_version": "1.0",
        "repository": REPOSITORY_ID,
        "head_sha": head,
        "status": "PASS",
        "branch": "main",
        "enforcement": "active",
        "pull_request_required": True,
        "required_branch_up_to_date": True,
        "force_push_allowed": False,
        "deletion_allowed": False,
        "conversation_resolution_required": True,
        "bypass_policy": "owner_emergency_receipt_only",
        "direct_push_probe": "blocked",
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            result["errors"].append(f"{field} must equal {value!r}")
    checks = payload.get("required_status_checks")
    if not isinstance(checks, list) or not all(
        isinstance(item, str) for item in checks
    ):
        result["errors"].append("required_status_checks must be a string list")
        checks = []
    missing = sorted(REQUIRED_STATUS_CHECKS - set(checks))
    if missing:
        result["errors"].append(f"missing required status checks: {', '.join(missing)}")
    ruleset_id = payload.get("ruleset_id")
    if not isinstance(ruleset_id, int) or ruleset_id <= 0:
        result["errors"].append("ruleset_id must be a positive integer")
    result["valid"] = not result["errors"]
    result["required_status_checks"] = sorted(set(checks))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-verified", action="store_true")
    parser.add_argument(
        "--require-projection-safety",
        action="store_true",
        help="fail when a historical projection lacks provenance or a stale warning",
    )
    args = parser.parse_args()

    head = git("rev-parse", "HEAD")
    dirty_paths = git("status", "--porcelain=v1").splitlines()
    heads = migration_heads()
    evidence = {
        relative: {
            "present": (REPO / relative).is_file(),
            "sha256": sha256(REPO / relative) if (REPO / relative).is_file() else None,
        }
        for relative in (*REQUIRED_EVIDENCE, *REMOTE_EVIDENCE)
    }
    remote_ci = inspect_remote_ci_receipt(REPO / REMOTE_EVIDENCE[0], head=head)
    main_ruleset = inspect_main_ruleset_receipt(REPO / REMOTE_EVIDENCE[1], head=head)
    evidence[REMOTE_EVIDENCE[0]].update(remote_ci)
    evidence[REMOTE_EVIDENCE[1]].update(main_ruleset)
    eval_data = json.loads(EVAL_REPORT.read_text(encoding="utf-8"))
    adr_files, missing_adrs = indexed_files(
        REPO / "document-rag-platform/docs/adr/README.md", "ADR-*.md"
    )
    runbook_files, missing_runbooks = indexed_files(
        REPO / "document-rag-platform/docs/runbooks/README.md", "*.md"
    )
    root_skeletons = [
        name
        for name in ("apps", "services", "tests", "packages", "infra")
        if (REPO / name).exists()
    ]
    projection_errors = []
    projection_status = {}
    for relative in HISTORICAL_PROJECTIONS:
        content = (REPO / relative).read_text(encoding="utf-8")
        status = inspect_historical_projection(content, head=head)
        projection_status[relative] = status
        if not status["safe"]:
            projection_errors.append(relative)

    checks = {
        "clean_worktree": not dirty_paths,
        "single_migration_head": len(heads) == 1,
        "required_local_evidence_present": all(
            evidence[path]["present"] for path in REQUIRED_EVIDENCE
        ),
        "remote_ci_evidence_valid": remote_ci["valid"],
        "main_ruleset_evidence_valid": main_ruleset["valid"],
        "synthetic_eval_has_no_quality_claim": (
            eval_data.get("classification") == "offline_contract_fixture"
            and eval_data.get("quality_claim") is False
            and "quality_gate" not in eval_data
        ),
        "no_root_application_skeletons": not root_skeletons,
        "adr_index_complete": not missing_adrs,
        "runbook_index_complete": not missing_runbooks,
        "historical_projections_warn_when_stale": not projection_errors,
    }
    verified = all(checks.values())
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": REPOSITORY_ID,
        "head_sha": head,
        "verified": verified,
        "checks": checks,
        "dirty_paths": dirty_paths,
        "migration_heads": heads,
        "evidence": evidence,
        "eval": {
            "report_sha256": sha256(EVAL_REPORT),
            "classification": eval_data.get("classification"),
            "quality_claim": eval_data.get("quality_claim"),
        },
        "documentation": {
            "adr_files": adr_files,
            "missing_from_adr_index": missing_adrs,
            "runbook_files": runbook_files,
            "missing_from_runbook_index": missing_runbooks,
            "projection_errors": projection_errors,
            "historical_projections": projection_status,
        },
        "root_skeletons": root_skeletons,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False))
    if args.require_verified and not verified:
        return 1
    if args.require_projection_safety and projection_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
