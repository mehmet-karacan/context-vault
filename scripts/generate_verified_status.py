#!/usr/bin/env python3
"""Generate a fail-closed, exact-checkout Context Vault status projection."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-verified", action="store_true")
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
    for relative in HISTORICAL_PROJECTIONS:
        content = (REPO / relative).read_text(encoding="utf-8")
        required_labels = (
            "historical/non-canonical",
            "last_verified_sha",
            "last_verified_at",
            "evidence_manifest",
            "STALE UYARISI",
        )
        if any(label not in content for label in required_labels):
            projection_errors.append(relative)

    checks = {
        "clean_worktree": not dirty_paths,
        "single_migration_head": len(heads) == 1,
        "required_local_evidence_present": all(
            evidence[path]["present"] for path in REQUIRED_EVIDENCE
        ),
        "remote_ci_evidence_present": evidence[REMOTE_EVIDENCE[0]]["present"],
        "main_ruleset_evidence_present": evidence[REMOTE_EVIDENCE[1]]["present"],
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
        "repository": "mehmet-karacan/context-vault",
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
        },
        "root_skeletons": root_skeletons,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 1 if args.require_verified and not verified else 0


if __name__ == "__main__":
    raise SystemExit(main())
