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
from typing import Any, Callable


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
CI_WORKFLOW_PATHS = {
    "ci-backend": ".github/workflows/ci-backend.yml",
    "ci-frontend": ".github/workflows/ci-frontend.yml",
    "ci-rag-contract": ".github/workflows/ci-rag-eval.yml",
    "ci-security": ".github/workflows/ci-security.yml",
}
CI_WORKFLOW_DISPLAY_NAMES = {
    "ci-backend": "ci-backend",
    "ci-frontend": "ci-frontend",
    "ci-rag-contract": "ci-rag-eval",
    "ci-security": "ci-security",
}
REQUIRED_STATUS_CHECKS = {
    "backend",
    "frontend",
    "offline-contract-fixture",
    "supply-chain",
    "codeql (python)",
    "codeql (javascript-typescript)",
    "check-ownership",
}
GITHUB_ACTIONS_INTEGRATION_ID = 15368
GitHubGet = Callable[[str], dict[str, Any]]


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


def github_api(endpoint: str) -> dict[str, Any]:
    """Read one authenticated GitHub REST resource, failing closed."""
    try:
        completed = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                "--header",
                "Accept: application/vnd.github+json",
                "--header",
                "X-GitHub-Api-Version: 2022-11-28",
                endpoint,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(completed.stdout)
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        raise RuntimeError("authenticated GitHub API lookup failed") from error
    if not isinstance(payload, dict):
        raise RuntimeError("authenticated GitHub API response is not an object")
    return payload


def _github_repository(payload: dict[str, Any]) -> str | None:
    repository = payload.get("repository")
    return repository.get("full_name") if isinstance(repository, dict) else None


def inspect_remote_ci_receipt(
    path: Path, *, head: str, github_get: GitHubGet = github_api
) -> dict[str, Any]:
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
    if result["errors"]:
        result["successful_workflows"] = []
        return result

    runs = payload.get("runs")
    successful: set[str] = set()
    seen_run_ids: set[int] = set()
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
            if run.get("status") != "completed":
                result["errors"].append(f"{name} status is not completed")
            if run.get("event") not in {"push", "pull_request"}:
                result["errors"].append(f"{name} event is not automatic")
            workflow_id = run.get("workflow_id")
            if not isinstance(workflow_id, int) or workflow_id <= 0:
                result["errors"].append(f"{name} workflow_id is invalid")
            run_attempt = run.get("run_attempt")
            if not isinstance(run_attempt, int) or run_attempt <= 0:
                result["errors"].append(f"{name} run_attempt is invalid")
            run_id = run.get("run_id")
            if not isinstance(run_id, int) or run_id <= 0:
                result["errors"].append(f"{name} run_id is invalid")
            elif run_id in seen_run_ids:
                result["errors"].append(f"{name} run_id is duplicated")
            else:
                seen_run_ids.add(run_id)
            run_url = run.get("run_url")
            if not isinstance(run_url, str) or not run_url.startswith(
                "https://github.com/mehmet-karacan/context-vault/actions/runs/"
            ):
                result["errors"].append(f"{name} run_url is invalid")
            if any(error.startswith(name) for error in result["errors"]):
                continue
            try:
                remote_run = github_get(f"repos/{REPOSITORY_ID}/actions/runs/{run_id}")
                remote_workflow = github_get(
                    f"repos/{REPOSITORY_ID}/actions/workflows/{workflow_id}"
                )
            except RuntimeError:
                result["errors"].append(f"{name} GitHub API lookup failed")
                continue
            authoritative = {
                "id": run_id,
                "name": name,
                "head_sha": head,
                "status": "completed",
                "conclusion": "success",
                "event": run.get("event"),
                "workflow_id": workflow_id,
                "run_attempt": run_attempt,
                "html_url": run_url,
                "repository": REPOSITORY_ID,
            }
            observed = {
                "id": remote_run.get("id"),
                "name": remote_run.get("name"),
                "head_sha": remote_run.get("head_sha"),
                "status": remote_run.get("status"),
                "conclusion": remote_run.get("conclusion"),
                "event": remote_run.get("event"),
                "workflow_id": remote_run.get("workflow_id"),
                "run_attempt": remote_run.get("run_attempt"),
                "html_url": remote_run.get("html_url"),
                "repository": _github_repository(remote_run),
            }
            if observed != authoritative:
                result["errors"].append(
                    f"{name} receipt does not match authenticated GitHub API state"
                )
                continue
            expected_workflow = {
                "id": workflow_id,
                "name": CI_WORKFLOW_DISPLAY_NAMES[name],
                "path": CI_WORKFLOW_PATHS[name],
                "state": "active",
            }
            observed_workflow = {
                field: remote_workflow.get(field) for field in expected_workflow
            }
            if observed_workflow != expected_workflow:
                result["errors"].append(
                    f"{name} workflow does not match authenticated GitHub API state"
                )
                continue
            successful.add(name)
    missing = sorted(REQUIRED_CI_WORKFLOWS - successful)
    if missing:
        result["errors"].append(f"missing successful workflows: {', '.join(missing)}")
    result["valid"] = not result["errors"]
    result["successful_workflows"] = sorted(successful)
    return result


def _rules_by_type(ruleset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rules = ruleset.get("rules")
    if not isinstance(rules, list):
        return {}
    return {
        rule["type"]: rule
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("type"), str)
    }


def _ruleset_targets_main(
    ruleset: dict[str, Any], *, default_branch: str | None
) -> bool:
    conditions = ruleset.get("conditions")
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    if not isinstance(ref_name, dict):
        return False
    include = ref_name.get("include")
    exclude = ref_name.get("exclude")
    if not isinstance(include, list) or not isinstance(exclude, list):
        return False
    targets_main = "refs/heads/main" in include or (
        "~DEFAULT_BRANCH" in include and default_branch == "main"
    )
    # GitHub applies fnmatch semantics to exclusions. A local approximation could
    # drift from that authority, so the verifier accepts only an empty exclusion
    # set for this dedicated main ruleset.
    return targets_main and not exclude


def _ruleset_status_checks(
    ruleset: dict[str, Any],
) -> tuple[dict[str, int | None], bool]:
    status_rule = _rules_by_type(ruleset).get("required_status_checks", {})
    parameters = status_rule.get("parameters")
    if not isinstance(parameters, dict):
        return {}, False
    raw_checks = parameters.get("required_status_checks")
    if not isinstance(raw_checks, list):
        return {}, False
    checks: dict[str, int | None] = {}
    for item in raw_checks:
        if not isinstance(item, dict) or not isinstance(item.get("context"), str):
            continue
        integration_id = item.get("integration_id")
        checks[item["context"]] = (
            integration_id if isinstance(integration_id, int) else None
        )
    return checks, parameters.get("strict_required_status_checks_policy") is True


def _ruleset_pull_request_policy(ruleset: dict[str, Any]) -> tuple[bool, bool]:
    pull_rule = _rules_by_type(ruleset).get("pull_request", {})
    parameters = pull_rule.get("parameters")
    if not isinstance(parameters, dict):
        return False, False
    return True, parameters.get("required_review_thread_resolution") is True


def _ruleset_owner_only_bypass(ruleset: dict[str, Any]) -> bool:
    actors = ruleset.get("bypass_actors")
    if not isinstance(actors, list) or not actors:
        return False
    # GitHub's repository role id 5 is the Admin role. This personal repository has
    # one administrator/owner, so this is the only remotely enforceable owner-only
    # bypass representation; the emergency-receipt procedure remains documented.
    return all(
        isinstance(actor, dict)
        and actor.get("actor_type") == "RepositoryRole"
        and actor.get("actor_id") == 5
        and actor.get("bypass_mode") == "always"
        for actor in actors
    )


def inspect_main_ruleset_receipt(
    path: Path, *, head: str, github_get: GitHubGet = github_api
) -> dict[str, Any]:
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
    if result["errors"]:
        result["required_status_checks"] = sorted(set(checks))
        return result

    try:
        ruleset = github_get(f"repos/{REPOSITORY_ID}/rulesets/{ruleset_id}")
        branch = github_get(f"repos/{REPOSITORY_ID}/branches/main")
        repository = github_get(f"repos/{REPOSITORY_ID}")
    except RuntimeError:
        result["errors"].append("authenticated GitHub API lookup failed")
        result["required_status_checks"] = sorted(set(checks))
        return result

    rules = _rules_by_type(ruleset)
    remote_check_sources, strict_checks = _ruleset_status_checks(ruleset)
    remote_checks = set(remote_check_sources)
    pr_required, conversation_resolution = _ruleset_pull_request_policy(ruleset)
    branch_commit = branch.get("commit")
    branch_sha = branch_commit.get("sha") if isinstance(branch_commit, dict) else None
    remote_state = {
        "ruleset_id": ruleset.get("id"),
        "target": ruleset.get("target"),
        "source_type": ruleset.get("source_type"),
        "source": ruleset.get("source"),
        "enforcement": ruleset.get("enforcement"),
        "default_branch": repository.get("default_branch"),
        "targets_main": _ruleset_targets_main(
            ruleset, default_branch=repository.get("default_branch")
        ),
        "main_head_sha": branch_sha,
        "pull_request_required": pr_required,
        "required_branch_up_to_date": strict_checks,
        "force_push_allowed": "non_fast_forward" not in rules,
        "deletion_allowed": "deletion" not in rules,
        "conversation_resolution_required": conversation_resolution,
        "owner_only_bypass": _ruleset_owner_only_bypass(ruleset),
    }
    expected_remote_state = {
        "ruleset_id": ruleset_id,
        "target": "branch",
        "source_type": "Repository",
        "source": REPOSITORY_ID,
        "enforcement": "active",
        "default_branch": "main",
        "targets_main": True,
        "main_head_sha": head,
        "pull_request_required": True,
        "required_branch_up_to_date": True,
        "force_push_allowed": False,
        "deletion_allowed": False,
        "conversation_resolution_required": True,
        "owner_only_bypass": True,
    }
    if remote_state != expected_remote_state:
        result["errors"].append(
            "receipt does not match authenticated GitHub ruleset/main state"
        )
    if set(checks) != remote_checks:
        result["errors"].append(
            "receipt status checks do not match authenticated GitHub ruleset"
        )
    missing_remote = sorted(REQUIRED_STATUS_CHECKS - remote_checks)
    if missing_remote:
        result["errors"].append(
            f"GitHub ruleset misses required status checks: {', '.join(missing_remote)}"
        )
    wrong_integration = sorted(
        context
        for context in REQUIRED_STATUS_CHECKS & remote_checks
        if remote_check_sources[context] != GITHUB_ACTIONS_INTEGRATION_ID
    )
    if wrong_integration:
        result["errors"].append(
            "GitHub ruleset status checks are not bound to GitHub Actions: "
            + ", ".join(wrong_integration)
        )
    result["valid"] = not result["errors"]
    result["required_status_checks"] = sorted(remote_checks)
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
