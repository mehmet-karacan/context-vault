"""Unit coverage for fail-closed historical-projection classification."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts/generate_verified_status.py"


def _module():
    spec = importlib.util.spec_from_file_location("cv_verified_status", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _projection(sha: str, *, warning: bool = True) -> str:
    suffix = "\n> **STALE UYARISI:** current checkout değildir.\n" if warning else "\n"
    return (
        "> **Sınıflandırma:** historical/non-canonical human projection\n"
        f"> **last_verified_sha:** `{sha}`\n"
        "> **last_verified_at:** `2026-09-05T00:00:00Z`\n"
        "> **evidence_manifest:** `evidence.json`\n" + suffix
    )


def test_current_projection_does_not_require_stale_warning() -> None:
    module = _module()
    head = "a" * 40
    status = module.inspect_historical_projection(
        _projection(head, warning=False), head=head
    )
    assert status["safe"] is True
    assert status["is_stale"] is False


def test_stale_projection_requires_explicit_warning() -> None:
    module = _module()
    status = module.inspect_historical_projection(
        _projection("a" * 40, warning=False), head="b" * 40
    )
    assert status["safe"] is False
    assert status["is_stale"] is True


def test_stale_projection_with_warning_is_safe_but_still_reported_stale() -> None:
    module = _module()
    status = module.inspect_historical_projection(_projection("a" * 40), head="b" * 40)
    assert status["safe"] is True
    assert status["is_stale"] is True
    assert status["stale_warning_present"] is True


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _valid_ci_receipt(module, head: str) -> dict:
    return {
        "schema_version": "1.0",
        "repository": module.REPOSITORY_ID,
        "head_sha": head,
        "status": "PASS",
        "runs": [
            {
                "workflow": workflow,
                "head_sha": head,
                "conclusion": "success",
                "status": "completed",
                "event": "push",
                "run_id": index,
                "workflow_id": 100 + index,
                "run_attempt": 1,
                "run_url": (
                    "https://github.com/mehmet-karacan/context-vault/actions/runs/"
                    f"{index}"
                ),
            }
            for index, workflow in enumerate(sorted(module.REQUIRED_CI_WORKFLOWS), 1)
        ],
    }


def _ci_api(module, payload: dict):
    runs = {run["run_id"]: run for run in payload["runs"]}
    workflows = {run["workflow_id"]: run for run in payload["runs"]}

    def get(endpoint: str) -> dict:
        if "/actions/workflows/" in endpoint:
            run = workflows[int(endpoint.rsplit("/", 1)[1])]
            return {
                "id": run["workflow_id"],
                "name": run["workflow"],
                "path": module.CI_WORKFLOW_PATHS[run["workflow"]],
                "state": "active",
            }
        run = runs[int(endpoint.rsplit("/", 1)[1])]
        return {
            "id": run["run_id"],
            "name": run["workflow"],
            "head_sha": run["head_sha"],
            "conclusion": run["conclusion"],
            "status": run["status"],
            "event": run["event"],
            "workflow_id": run["workflow_id"],
            "run_attempt": run["run_attempt"],
            "html_url": run["run_url"],
            "repository": {"full_name": "mehmet-karacan/context-vault"},
        }

    return get


def test_remote_ci_receipt_rejects_presence_only_or_wrong_head(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "ci.json"
    _write_json(path, {})
    assert module.inspect_remote_ci_receipt(path, head="a" * 40)["valid"] is False

    payload = _valid_ci_receipt(module, "a" * 40)
    payload["head_sha"] = "b" * 40
    _write_json(path, payload)
    assert module.inspect_remote_ci_receipt(path, head="a" * 40)["valid"] is False


def test_remote_ci_receipt_requires_every_successful_automatic_run(
    tmp_path: Path,
) -> None:
    module = _module()
    head = "a" * 40
    path = tmp_path / "ci.json"
    payload = _valid_ci_receipt(module, head)
    payload["runs"][0]["event"] = "workflow_dispatch"
    _write_json(path, payload)
    assert (
        module.inspect_remote_ci_receipt(
            path, head=head, github_get=_ci_api(module, payload)
        )["valid"]
        is False
    )

    payload = _valid_ci_receipt(module, head)
    _write_json(path, payload)
    assert (
        module.inspect_remote_ci_receipt(
            path, head=head, github_get=_ci_api(module, payload)
        )["valid"]
        is True
    )


def test_hand_authored_ci_receipt_fails_when_github_disagrees(tmp_path: Path) -> None:
    module = _module()
    head = "a" * 40
    path = tmp_path / "ci.json"
    payload = _valid_ci_receipt(module, head)
    _write_json(path, payload)

    def disagree(_endpoint: str) -> dict:
        return {
            "id": 999,
            "name": "forged",
            "head_sha": "b" * 40,
            "conclusion": "success",
            "status": "completed",
            "event": "push",
            "workflow_id": 999,
            "run_attempt": 1,
            "html_url": "https://github.com/mehmet-karacan/context-vault/actions/runs/999",
            "repository": {"full_name": module.REPOSITORY_ID},
        }

    result = module.inspect_remote_ci_receipt(path, head=head, github_get=disagree)
    assert result["valid"] is False
    assert any("authenticated GitHub API state" in error for error in result["errors"])


def _ruleset_api(
    module,
    head: str,
    checks: list[str],
    *,
    excludes: list[str] | None = None,
    integration_id: int | None = None,
    default_branch: str = "main",
):
    if integration_id is None:
        integration_id = module.GITHUB_ACTIONS_INTEGRATION_ID

    def get(endpoint: str) -> dict:
        if endpoint.endswith("/branches/main"):
            return {"commit": {"sha": head}}
        if endpoint == f"repos/{module.REPOSITORY_ID}":
            return {"default_branch": default_branch}
        return {
            "id": 42,
            "name": "Protect main",
            "target": "branch",
            "source_type": "Repository",
            "source": module.REPOSITORY_ID,
            "enforcement": "active",
            "bypass_actors": [
                {
                    "actor_id": 5,
                    "actor_type": "RepositoryRole",
                    "bypass_mode": "always",
                }
            ],
            "conditions": {
                "ref_name": {
                    "include": ["~DEFAULT_BRANCH"],
                    "exclude": excludes or [],
                }
            },
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "pull_request",
                    "parameters": {"required_review_thread_resolution": True},
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [
                            {
                                "context": context,
                                "integration_id": integration_id,
                            }
                            for context in checks
                        ],
                    },
                },
            ],
        }

    return get


def _valid_ruleset_receipt(module, head: str) -> dict:
    return {
        "schema_version": "1.0",
        "repository": module.REPOSITORY_ID,
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
        "required_status_checks": sorted(module.REQUIRED_STATUS_CHECKS),
        "ruleset_id": 42,
    }


def test_ruleset_receipt_rejects_unprotected_or_incomplete_state(
    tmp_path: Path,
) -> None:
    module = _module()
    head = "a" * 40
    path = tmp_path / "ruleset.json"
    payload = _valid_ruleset_receipt(module, head)
    _write_json(path, payload)
    assert (
        module.inspect_main_ruleset_receipt(
            path,
            head=head,
            github_get=_ruleset_api(
                module, head, sorted(module.REQUIRED_STATUS_CHECKS)
            ),
        )["valid"]
        is True
    )

    payload["force_push_allowed"] = True
    payload["required_status_checks"] = []
    _write_json(path, payload)
    assert module.inspect_main_ruleset_receipt(path, head=head)["valid"] is False


def test_hand_authored_ruleset_receipt_fails_when_github_disagrees(
    tmp_path: Path,
) -> None:
    module = _module()
    head = "a" * 40
    path = tmp_path / "ruleset.json"
    payload = _valid_ruleset_receipt(module, head)
    _write_json(path, payload)
    result = module.inspect_main_ruleset_receipt(
        path,
        head=head,
        github_get=_ruleset_api(
            module, "b" * 40, sorted(module.REQUIRED_STATUS_CHECKS)
        ),
    )
    assert result["valid"] is False
    assert any("GitHub ruleset/main state" in error for error in result["errors"])


def test_ruleset_rejects_pattern_exclusion_that_can_remove_main(
    tmp_path: Path,
) -> None:
    module = _module()
    head = "a" * 40
    path = tmp_path / "ruleset.json"
    payload = _valid_ruleset_receipt(module, head)
    _write_json(path, payload)
    result = module.inspect_main_ruleset_receipt(
        path,
        head=head,
        github_get=_ruleset_api(
            module,
            head,
            sorted(module.REQUIRED_STATUS_CHECKS),
            excludes=["refs/heads/ma*"],
        ),
    )
    assert result["valid"] is False


def test_ruleset_rejects_status_checks_from_untrusted_integration(
    tmp_path: Path,
) -> None:
    module = _module()
    head = "a" * 40
    path = tmp_path / "ruleset.json"
    payload = _valid_ruleset_receipt(module, head)
    _write_json(path, payload)
    result = module.inspect_main_ruleset_receipt(
        path,
        head=head,
        github_get=_ruleset_api(
            module,
            head,
            sorted(module.REQUIRED_STATUS_CHECKS),
            integration_id=999999,
        ),
    )
    assert result["valid"] is False
    assert any("not bound to GitHub Actions" in error for error in result["errors"])


def test_default_branch_alias_must_resolve_to_main(tmp_path: Path) -> None:
    module = _module()
    head = "a" * 40
    path = tmp_path / "ruleset.json"
    payload = _valid_ruleset_receipt(module, head)
    _write_json(path, payload)
    result = module.inspect_main_ruleset_receipt(
        path,
        head=head,
        github_get=_ruleset_api(
            module,
            head,
            sorted(module.REQUIRED_STATUS_CHECKS),
            default_branch="dev",
        ),
    )
    assert result["valid"] is False
    assert any("GitHub ruleset/main state" in error for error in result["errors"])
