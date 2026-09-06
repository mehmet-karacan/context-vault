"""Fail-closed tests for the automatic A11 backup scheduler contract."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[4] / "scripts/backup_scheduler.py"
SPEC = importlib.util.spec_from_file_location("backup_scheduler", SCRIPT)
assert SPEC and SPEC.loader
scheduler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduler)

ENV_NAMES = {
    "database_url_env": "CV_TEST_DATABASE_URL",
    "minio_endpoint_env": "CV_TEST_MINIO_ENDPOINT",
    "minio_access_key_env": "CV_TEST_MINIO_ACCESS",
    "minio_secret_key_env": "CV_TEST_MINIO_SECRET",
    "minio_bucket_env": "CV_TEST_MINIO_BUCKET",
    "encryption_key_env": "CV_TEST_ENCRYPTION_KEY",
}


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    script = repo / "scripts/backup_restore.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise SystemExit(99)\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Backup Scheduler Test")
    _git(repo, "config", "user.email", "backup@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "private-backups"
    root.mkdir(mode=0o700)
    return root


def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for index, name in enumerate(ENV_NAMES.values()):
        monkeypatch.setenv(name, f"private-value-{index}")
    monkeypatch.setenv("UNRELATED_PRIVATE_VALUE", "must-not-propagate")


def _arguments(
    repo: Path,
    root: Path,
    revision: str,
    **overrides,
) -> dict:
    values = {
        "repo": repo,
        "backup_root": root,
        "expected_revision": revision,
        "interval_seconds": 21_600,
        "retention_days": 30,
        "postgres_container": "synthetic-postgres",
        **ENV_NAMES,
        "dry_run": False,
        "now": datetime(2026, 9, 6, 7, 15, tzinfo=timezone.utc),
        "timeout_seconds": 600,
    }
    values.update(overrides)
    return values


def _write_child_success(
    command: list[str],
    *,
    revision: str,
    retention_days: int,
) -> None:
    backup_dir = Path(command[command.index("--backup-dir") + 1])
    receipt_path = Path(command[command.index("--json-output") + 1])
    backup_dir.mkdir(mode=0o700)
    manifest = backup_dir / "backup-manifest.private.json"
    manifest.write_text('{"private":"synthetic"}\n', encoding="utf-8")
    manifest.chmod(0o600)
    manifest_sha = scheduler._stable_file_sha256(manifest)
    payload = {
        "schema_version": "1.0",
        "receipt_type": "backup-complete",
        "status": "PASS",
        "repository_revision": revision,
        "backup_id_hash": "a" * 64,
        "retention_days": retention_days,
        "started_at_utc": "2026-09-06T06:00:01Z",
        "completed_at_utc": "2026-09-06T06:00:02Z",
        "backup_manifest_sha256": manifest_sha,
        "source_database_binding": {
            "container_id_sha256": "d" * 64,
            "system_identifier_sha256": "e" * 64,
            "database_sha256": "f" * 64,
        },
        "source_object_binding": {
            "resolved_identity_sha256": "1" * 64,
            "bucket_sha256": "2" * 64,
        },
        "postgres_dump_sha256": "b" * 64,
        "object_inventory_digest": "c" * 64,
        "object_count": 2,
        "object_bytes": 128,
        "encrypted_envelope_count": 2,
        "versioning_enabled": True,
        "source_mutated": False,
        "credential_values_retained": False,
        "raw_object_names_retained_in_receipt": False,
        "private_manifest_contains_restore_keys": True,
        "restore_verified": False,
        "release_gate_eligible": False,
    }
    descriptor = os.open(
        receipt_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")


def test_dry_run_binds_exact_revision_without_credentials_or_effects(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    result = scheduler.run_schedule(
        **_arguments(
            repo,
            tmp_path / "does-not-exist",
            revision,
            dry_run=True,
        )
    )

    assert result["status"] == "DRY_RUN"
    assert result["repository_revision"] == revision
    assert result["schedule_slot_utc"] == "2026-09-06T06:00:00Z"
    assert result["backup_invoked"] is False
    assert result["credential_values_retained"] is False


def test_success_calls_backup_restore_with_names_only_and_private_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = _repo(tmp_path)
    root = _root(tmp_path)
    _environment(monkeypatch)
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        rendered = " ".join(command)
        assert "private-value" not in rendered
        assert "UNRELATED_PRIVATE_VALUE" not in kwargs["env"]
        assert set(ENV_NAMES.values()).issubset(kwargs["env"])
        assert kwargs["cwd"] == repo
        assert kwargs["timeout"] == 600
        _write_child_success(command, revision=revision, retention_days=30)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = scheduler.run_schedule(
        **_arguments(repo, root, revision),
        run=run,
    )

    assert len(calls) == 1
    assert calls[0][1].endswith("scripts/backup_restore.py")
    assert result["status"] == "PASS"
    assert result["backup_invoked"] is True
    assert result["object_count"] == result["encrypted_envelope_count"] == 2
    assert result["retention_not_before_utc"] == "2026-10-06T06:00:02Z"
    assert result["release_gate_eligible"] is False


def test_completed_slot_is_idempotent_and_never_reinvokes_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = _repo(tmp_path)
    root = _root(tmp_path)
    _environment(monkeypatch)

    def first_run(command, **_kwargs):
        _write_child_success(command, revision=revision, retention_days=30)
        return subprocess.CompletedProcess(command, 0, "", "")

    first = scheduler.run_schedule(
        **_arguments(repo, root, revision),
        run=first_run,
    )
    second = scheduler.run_schedule(
        **_arguments(repo, root, revision),
        run=lambda *_args, **_kwargs: pytest.fail("backup child was reinvoked"),
    )

    assert first["status"] == "PASS"
    assert second["status"] == "SKIPPED"
    assert second["idempotent_replay"] is True
    assert second["backup_invoked"] is False
    assert second["backup_manifest_sha256"] == first["backup_manifest_sha256"]


def test_revision_mismatch_and_dirty_tree_fail_before_child_or_credentials(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    root = _root(tmp_path)
    with pytest.raises(scheduler.SchedulerError) as mismatch:
        scheduler.run_schedule(
            **_arguments(repo, root, "a" * 40),
            run=lambda *_args, **_kwargs: pytest.fail("child invoked"),
        )
    assert mismatch.value.code == "REVISION_MISMATCH"

    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(scheduler.SchedulerError) as dirty:
        scheduler.run_schedule(
            **_arguments(repo, root, revision),
            run=lambda *_args, **_kwargs: pytest.fail("child invoked"),
        )
    assert dirty.value.code == "DIRTY_REPOSITORY"


def test_nonblocking_lock_rejects_overlap(tmp_path: Path) -> None:
    repo, revision = _repo(tmp_path)
    root = _root(tmp_path)
    lock_path = root / ".context-vault-backup.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(scheduler.SchedulerError) as overlap:
            scheduler.run_schedule(
                **_arguments(repo, root, revision),
                run=lambda *_args, **_kwargs: pytest.fail("child invoked"),
            )
        assert overlap.value.code == "OVERLAP_ACTIVE"
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_lock_and_prior_receipt_symlinks_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = _repo(tmp_path)
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("not a lock\n", encoding="utf-8")
    (root / ".context-vault-backup.lock").symlink_to(outside)
    with pytest.raises(scheduler.SchedulerError) as unsafe_lock:
        scheduler.run_schedule(**_arguments(repo, root, revision))
    assert unsafe_lock.value.code == "LOCK_ADMISSION_FAILED"

    (root / ".context-vault-backup.lock").unlink()
    _environment(monkeypatch)

    def first_run(command, **_kwargs):
        _write_child_success(command, revision=revision, retention_days=30)
        return subprocess.CompletedProcess(command, 0, "", "")

    scheduler.run_schedule(
        **_arguments(repo, root, revision),
        run=first_run,
    )
    receipt = next((root / "receipts").glob("*.json"))
    original = tmp_path / "original-receipt.json"
    receipt.rename(original)
    receipt.symlink_to(original)
    with pytest.raises(scheduler.SchedulerError) as unsafe_receipt:
        scheduler.run_schedule(**_arguments(repo, root, revision))
    assert unsafe_receipt.value.code == "CHILD_RECEIPT_UNAVAILABLE"


def test_incomplete_or_tampered_prior_slot_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = _repo(tmp_path)
    root = _root(tmp_path)
    _environment(monkeypatch)
    (root / "backups").mkdir(mode=0o700)
    (root / "receipts").mkdir(mode=0o700)
    slot_key = f"20260906T060000Z-{revision[:12]}"
    (root / "backups" / slot_key).mkdir(mode=0o700)

    with pytest.raises(scheduler.SchedulerError) as incomplete:
        scheduler.run_schedule(
            **_arguments(repo, root, revision),
            run=lambda *_args, **_kwargs: pytest.fail("child invoked"),
        )
    assert incomplete.value.code == "INCOMPLETE_PRIOR_ATTEMPT"


def test_unsafe_backup_roots_and_environment_names_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = _repo(tmp_path)
    inside = repo / "private"
    inside.mkdir(mode=0o700)
    _environment(monkeypatch)
    with pytest.raises(scheduler.SchedulerError) as root_error:
        scheduler.run_schedule(**_arguments(repo, inside, revision))
    assert root_error.value.code == "BACKUP_ROOT_INSIDE_REPOSITORY"

    external = _root(tmp_path)
    with pytest.raises(scheduler.SchedulerError) as env_error:
        scheduler.run_schedule(
            **_arguments(
                repo,
                external,
                revision,
                minio_secret_key_env="PATH",
            )
        )
    assert env_error.value.code in {
        "DUPLICATE_ENVIRONMENT_NAME",
        "INVALID_ENVIRONMENT_NAME",
    }


def test_child_failure_is_terminal_and_does_not_expose_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = _repo(tmp_path)
    root = _root(tmp_path)
    _environment(monkeypatch)

    with pytest.raises(scheduler.SchedulerError) as failure:
        scheduler.run_schedule(
            **_arguments(repo, root, revision),
            run=lambda command, **_kwargs: subprocess.CompletedProcess(
                command,
                2,
                "private-output",
                "private-error",
            ),
        )
    assert failure.value.code == "CHILD_BACKUP_FAILED"
    assert failure.value.effects_may_have_started is True
    assert "private" not in str(failure.value)


def test_cli_reserves_private_receipt_and_rejects_overwrite(tmp_path: Path) -> None:
    repo, revision = _repo(tmp_path)
    output = tmp_path / "receipts" / "dry-run.json"
    arguments = [
        "--repo",
        str(repo),
        "--backup-root",
        str(tmp_path / "not-created"),
        "--expected-revision",
        revision,
        "--postgres-container",
        "synthetic-postgres",
        "--database-url-env",
        "CV_TEST_DATABASE_URL",
        "--minio-endpoint-env",
        "CV_TEST_MINIO_ENDPOINT",
        "--minio-access-key-env",
        "CV_TEST_MINIO_ACCESS",
        "--minio-secret-key-env",
        "CV_TEST_MINIO_SECRET",
        "--minio-bucket-env",
        "CV_TEST_MINIO_BUCKET",
        "--encryption-key-env",
        "CV_TEST_ENCRYPTION_KEY",
        "--dry-run",
        "--json-output",
        str(output),
    ]
    assert scheduler.main(arguments) == 0
    assert json.loads(output.read_text())["status"] == "DRY_RUN"
    assert output.stat().st_mode & 0o777 == 0o600
    assert scheduler.main(arguments) == 2


def test_public_receipt_encoder_rejects_schema_and_path_drift(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    receipt = scheduler.run_schedule(
        **_arguments(repo, tmp_path / "unused", revision, dry_run=True)
    )
    assert json.loads(scheduler._encode_public(receipt))["status"] == "DRY_RUN"
    receipt["backup_path"] = "/private/location"
    with pytest.raises(scheduler.SchedulerError) as drift:
        scheduler._encode_public(receipt)
    assert drift.value.code == "PUBLIC_RECEIPT_SCHEMA_DRIFT"


def test_required_runbooks_expose_executable_bounded_drills() -> None:
    root = SCRIPT.parents[1] / "document-rag-platform/docs/runbooks"
    expected = {
        "a11-backup-restore.md": [
            "scripts/backup_scheduler.py",
            "--expected-revision",
            "retention_not_before_utc",
            "No production scheduler execution is claimed",
        ],
        "reindex-profile-change.md": [
            "test_retry_after_partial_failure_clears_stale_chunks_before_reindexing",
            "test_reindex_uses_same_orchestrator_and_preserves_active_version_on_failure",
            "Never point this command at user data",
        ],
        "stuck-job-outbox-lease.md": [
            "test_policy_outbox_lease_and_orphan_recovery_paths",
            "CV_DISPOSABLE_DATABASE_URL",
            "Never aim",
        ],
        "object-gc-quarantine.md": [
            "test_delete_retention_citation_hold_and_gc_are_safe_and_idempotent",
            "does not connect to MinIO or delete a real object",
        ],
        "repository-ingestion-incident.md": [
            "test_repository_url_rejects_non_public_dns_results",
            "no clone or hostile network request",
        ],
        "degraded-readiness.md": [
            "test_optional_provider_returns_capability_degraded_without_details",
            "does not stop a service or call a provider",
        ],
        "security-incident.md": [
            "test_redaction.py",
            "performs no revocation",
        ],
        "release-rollback.md": [
            "test_rc_open_p1_and_dirty_worktree_fail_closed",
            "does not sign",
        ],
    }
    for name, snippets in expected.items():
        content = (root / name).read_text(encoding="utf-8")
        for snippet in snippets:
            assert snippet in content, f"{name}: missing {snippet}"


@pytest.mark.parametrize("interval", [299, 1000, 86401])
def test_schedule_interval_policy_is_bounded(interval: int) -> None:
    with pytest.raises(scheduler.SchedulerError) as error:
        scheduler._schedule_slot(datetime.now(timezone.utc), interval)
    assert error.value.code == "INVALID_SCHEDULE_INTERVAL"
