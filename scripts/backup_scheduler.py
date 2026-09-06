#!/usr/bin/env python3
"""Fail-closed scheduler wrapper for Context Vault database/object backups.

The wrapper owns schedule-slot idempotency, a non-blocking single-run lock,
exact clean-revision admission, private output layout and retention metadata. It
delegates the actual PostgreSQL plus MinIO snapshot to ``backup_restore.py`` and
never places credential values on the command line or in a public receipt.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


TOOL_VERSION = "1.0.0"
MAX_JSON_BYTES = 1024 * 1024
MAX_SUBPROCESS_OUTPUT_BYTES = 16 * 1024
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
Run = Callable[..., subprocess.CompletedProcess[str]]


class SchedulerError(RuntimeError):
    """A scheduler admission or terminal gate failed."""

    def __init__(
        self,
        code: str,
        *,
        effects_may_have_started: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.effects_may_have_started = effects_may_have_started


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise SchedulerError("INVALID_CLOCK")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SchedulerError("INVALID_CHILD_RECEIPT")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchedulerError("INVALID_CHILD_RECEIPT") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SchedulerError("INVALID_CHILD_RECEIPT")
    return parsed


def _schedule_slot(now: datetime, interval_seconds: int) -> datetime:
    if (
        not isinstance(interval_seconds, int)
        or isinstance(interval_seconds, bool)
        or not 300 <= interval_seconds <= 86_400
        or 86_400 % interval_seconds
    ):
        raise SchedulerError("INVALID_SCHEDULE_INTERVAL")
    current = now.astimezone(timezone.utc)
    epoch = int(current.timestamp())
    slot_epoch = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(slot_epoch, timezone.utc)


def _slot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _base_subprocess_env() -> dict[str, str]:
    allowed = {
        "PATH",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def _git(repo: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=_base_subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchedulerError("GIT_INSPECTION_FAILED") from exc
    if len(completed.stdout) > MAX_JSON_BYTES or len(completed.stderr) > 8192:
        raise SchedulerError("GIT_OUTPUT_EXCEEDED_POLICY")
    return completed.stdout.strip()


def _repository_revision(repo: Path, *, require_clean: bool) -> str:
    revision = _git(repo, "rev-parse", "HEAD")
    if not GIT_SHA.fullmatch(revision):
        raise SchedulerError("INVALID_REPOSITORY_REVISION")
    if require_clean and _git(
        repo, "status", "--porcelain", "--untracked-files=normal"
    ):
        raise SchedulerError("DIRTY_REPOSITORY")
    return revision


def _secure_directory(path: Path, *, create: bool) -> Path:
    if create:
        try:
            path.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SchedulerError("PRIVATE_DIRECTORY_UNAVAILABLE") from exc
    try:
        supplied = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise SchedulerError("PRIVATE_DIRECTORY_UNAVAILABLE") from exc
    if (
        stat.S_ISLNK(supplied.st_mode)
        or not stat.S_ISDIR(resolved_stat.st_mode)
        or stat.S_IMODE(resolved_stat.st_mode) != 0o700
        or resolved_stat.st_uid != os.getuid()
    ):
        raise SchedulerError("PRIVATE_DIRECTORY_UNSAFE")
    return resolved


def _backup_root(repo: Path, supplied: Path) -> Path:
    root = _secure_directory(supplied, create=False)
    try:
        root.relative_to(repo.resolve(strict=True))
    except ValueError:
        return root
    raise SchedulerError("BACKUP_ROOT_INSIDE_REPOSITORY")


@contextmanager
def _single_run_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".context-vault-backup.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        lock_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or stat.S_IMODE(lock_stat.st_mode) != 0o600
            or lock_stat.st_uid != os.getuid()
        ):
            raise SchedulerError("LOCK_FILE_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except SchedulerError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise
    except BlockingIOError as exc:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise SchedulerError("OVERLAP_ACTIVE") from exc
    except OSError as exc:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise SchedulerError("LOCK_ADMISSION_FAILED") from exc
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _stable_json(path: Path) -> tuple[dict[str, Any], str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SchedulerError("CHILD_RECEIPT_UNAVAILABLE") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > MAX_JSON_BYTES
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
        ):
            raise SchedulerError("CHILD_RECEIPT_UNSAFE")
        raw = os.read(descriptor, MAX_JSON_BYTES + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SchedulerError("CHILD_RECEIPT_CHANGED")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchedulerError("INVALID_CHILD_RECEIPT") from exc
    if not isinstance(payload, dict):
        raise SchedulerError("INVALID_CHILD_RECEIPT")
    return payload, _sha256_bytes(raw)


def _stable_file_sha256(path: Path, maximum: int = 16 * 1024 * 1024) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SchedulerError("BACKUP_MANIFEST_UNAVAILABLE") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > maximum
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
        ):
            raise SchedulerError("BACKUP_MANIFEST_UNSAFE")
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SchedulerError("BACKUP_MANIFEST_CHANGED")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _validate_child_receipt(
    *,
    receipt_path: Path,
    backup_dir: Path,
    revision: str,
    retention_days: int,
    schedule_slot: datetime,
    interval_seconds: int,
    observed_at: datetime,
) -> dict[str, Any]:
    receipt, receipt_sha = _stable_json(receipt_path)
    required_fields = {
        "schema_version",
        "receipt_type",
        "status",
        "repository_revision",
        "backup_id_hash",
        "backup_manifest_sha256",
        "source_database_binding",
        "source_object_binding",
        "started_at_utc",
        "completed_at_utc",
        "retention_days",
        "postgres_dump_sha256",
        "object_inventory_digest",
        "object_count",
        "object_bytes",
        "versioning_enabled",
        "encrypted_envelope_count",
        "source_mutated",
        "raw_object_names_retained_in_receipt",
        "private_manifest_contains_restore_keys",
        "credential_values_retained",
        "restore_verified",
        "release_gate_eligible",
    }
    object_count = receipt.get("object_count")
    encrypted_count = receipt.get("encrypted_envelope_count")
    manifest_sha = receipt.get("backup_manifest_sha256")
    database_binding = receipt.get("source_database_binding")
    object_binding = receipt.get("source_object_binding")
    if (
        set(receipt) != required_fields
        or receipt.get("schema_version") != "1.0"
        or receipt.get("receipt_type") != "backup-complete"
        or receipt.get("status") != "PASS"
        or receipt.get("repository_revision") != revision
        or receipt.get("retention_days") != retention_days
        or not isinstance(object_count, int)
        or isinstance(object_count, bool)
        or not 1 <= object_count <= 100_000
        or encrypted_count != object_count
        or receipt.get("versioning_enabled") is not True
        or receipt.get("source_mutated") is not False
        or receipt.get("credential_values_retained") is not False
        or receipt.get("raw_object_names_retained_in_receipt") is not False
        or receipt.get("private_manifest_contains_restore_keys") is not True
        or receipt.get("restore_verified") is not False
        or receipt.get("release_gate_eligible") is not False
        or not isinstance(receipt.get("object_bytes"), int)
        or isinstance(receipt.get("object_bytes"), bool)
        or receipt["object_bytes"] <= 0
        or not isinstance(manifest_sha, str)
        or not SHA256.fullmatch(manifest_sha)
        or not all(
            isinstance(receipt.get(field), str) and SHA256.fullmatch(receipt[field])
            for field in (
                "backup_id_hash",
                "postgres_dump_sha256",
                "object_inventory_digest",
            )
        )
        or not isinstance(database_binding, dict)
        or set(database_binding)
        != {"container_id_sha256", "system_identifier_sha256", "database_sha256"}
        or not all(
            isinstance(value, str) and SHA256.fullmatch(value)
            for value in database_binding.values()
        )
        or not isinstance(object_binding, dict)
        or set(object_binding) != {"resolved_identity_sha256", "bucket_sha256"}
        or not all(
            isinstance(value, str) and SHA256.fullmatch(value)
            for value in object_binding.values()
        )
    ):
        raise SchedulerError("INVALID_CHILD_RECEIPT")
    secure_backup_dir = _secure_directory(backup_dir, create=False)
    actual_manifest_sha = _stable_file_sha256(
        secure_backup_dir / "backup-manifest.private.json"
    )
    if actual_manifest_sha != manifest_sha:
        raise SchedulerError("BACKUP_MANIFEST_BINDING_MISMATCH")
    started = _parse_utc(receipt.get("started_at_utc"))
    completed = _parse_utc(receipt.get("completed_at_utc"))
    current = observed_at.astimezone(timezone.utc)
    if (
        started < schedule_slot
        or completed < started
        or completed >= schedule_slot + timedelta(seconds=interval_seconds)
        or completed > current + timedelta(minutes=5)
    ):
        raise SchedulerError("CHILD_RECEIPT_TIME_OUT_OF_SLOT")
    return {
        "child_receipt_sha256": receipt_sha,
        "backup_manifest_sha256": manifest_sha,
        "object_count": object_count,
        "encrypted_envelope_count": encrypted_count,
        "backup_completed_at_utc": _utc_text(completed),
        "retention_not_before_utc": _utc_text(
            completed + timedelta(days=retention_days)
        ),
    }


def _validate_named_environment(names: list[str]) -> dict[str, str]:
    if len(names) != len(set(names)):
        raise SchedulerError("DUPLICATE_ENVIRONMENT_NAME")
    projected = _base_subprocess_env()
    for name in names:
        if not ENV_NAME.fullmatch(name) or name in projected:
            raise SchedulerError("INVALID_ENVIRONMENT_NAME")
        value = os.environ.get(name)
        if not value:
            raise SchedulerError("REQUIRED_ENVIRONMENT_MISSING")
        projected[name] = value
    return projected


def _result(
    *,
    status: str,
    revision: str,
    slot: datetime,
    interval_seconds: int,
    retention_days: int,
    backup_invoked: bool,
    idempotent_replay: bool,
    child: dict[str, Any] | None = None,
) -> dict[str, Any]:
    child = child or {}
    return {
        "schema_version": "1.0",
        "receipt_type": "automatic-backup-schedule",
        "tool_version": TOOL_VERSION,
        "status": status,
        "repository_revision": revision,
        "schedule_slot_utc": _utc_text(slot),
        "interval_seconds": interval_seconds,
        "retention_days": retention_days,
        "backup_invoked": backup_invoked,
        "idempotent_replay": idempotent_replay,
        "overlap_detected": False,
        "child_receipt_sha256": child.get("child_receipt_sha256"),
        "backup_manifest_sha256": child.get("backup_manifest_sha256"),
        "backup_completed_at_utc": child.get("backup_completed_at_utc"),
        "retention_not_before_utc": child.get("retention_not_before_utc"),
        "object_count": child.get("object_count"),
        "encrypted_envelope_count": child.get("encrypted_envelope_count"),
        "source_mutated": False,
        "credential_values_retained": False,
        "raw_paths_retained": False,
        "release_gate_eligible": False,
    }


def run_schedule(
    *,
    repo: Path,
    backup_root: Path,
    expected_revision: str,
    interval_seconds: int,
    retention_days: int,
    postgres_container: str,
    database_url_env: str,
    minio_endpoint_env: str,
    minio_access_key_env: str,
    minio_secret_key_env: str,
    minio_bucket_env: str,
    encryption_key_env: str,
    dry_run: bool,
    now: datetime | None = None,
    timeout_seconds: int = 1800,
    run: Run = subprocess.run,
) -> dict[str, Any]:
    """Admit or execute one automatic backup schedule slot."""

    if (
        not GIT_SHA.fullmatch(expected_revision)
        or not isinstance(retention_days, int)
        or isinstance(retention_days, bool)
        or not 1 <= retention_days <= 3650
        or not 60 <= timeout_seconds <= 3600
        or not postgres_container
    ):
        raise SchedulerError("INVALID_POLICY")
    repo = repo.resolve(strict=True)
    revision = _repository_revision(repo, require_clean=not dry_run)
    if revision != expected_revision:
        raise SchedulerError("REVISION_MISMATCH")
    observed_at = now or datetime.now(timezone.utc)
    slot = _schedule_slot(observed_at, interval_seconds)
    if dry_run:
        return _result(
            status="DRY_RUN",
            revision=revision,
            slot=slot,
            interval_seconds=interval_seconds,
            retention_days=retention_days,
            backup_invoked=False,
            idempotent_replay=False,
        )

    root = _backup_root(repo, backup_root)
    with _single_run_lock(root):
        backups = _secure_directory(root / "backups", create=True)
        receipts = _secure_directory(root / "receipts", create=True)
        slot_key = f"{_slot_id(slot)}-{revision[:12]}"
        backup_dir = backups / slot_key
        child_receipt_path = receipts / f"{slot_key}.json"
        if child_receipt_path.exists():
            child = _validate_child_receipt(
                receipt_path=child_receipt_path,
                backup_dir=backup_dir,
                revision=revision,
                retention_days=retention_days,
                schedule_slot=slot,
                interval_seconds=interval_seconds,
                observed_at=observed_at,
            )
            return _result(
                status="SKIPPED",
                revision=revision,
                slot=slot,
                interval_seconds=interval_seconds,
                retention_days=retention_days,
                backup_invoked=False,
                idempotent_replay=True,
                child=child,
            )
        if backup_dir.exists():
            raise SchedulerError("INCOMPLETE_PRIOR_ATTEMPT")

        names = [
            database_url_env,
            minio_endpoint_env,
            minio_access_key_env,
            minio_secret_key_env,
            minio_bucket_env,
            encryption_key_env,
        ]
        child_env = _validate_named_environment(names)
        child_script = repo / "scripts/backup_restore.py"
        if not child_script.is_file() or child_script.is_symlink():
            raise SchedulerError("BACKUP_TOOL_UNAVAILABLE")
        command = [
            sys.executable,
            str(child_script),
            "--repo",
            str(repo),
            "--backup",
            "--retention-days",
            str(retention_days),
            "--database-url-env",
            database_url_env,
            "--postgres-container",
            postgres_container,
            "--minio-endpoint-env",
            minio_endpoint_env,
            "--minio-access-key-env",
            minio_access_key_env,
            "--minio-secret-key-env",
            minio_secret_key_env,
            "--minio-bucket-env",
            minio_bucket_env,
            "--encryption-key-env",
            encryption_key_env,
            "--backup-dir",
            str(backup_dir),
            "--json-output",
            str(child_receipt_path),
        ]
        try:
            completed = run(
                command,
                cwd=repo,
                env=child_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SchedulerError(
                "CHILD_BACKUP_EXECUTION_FAILED", effects_may_have_started=True
            ) from exc
        if (
            len(completed.stdout.encode("utf-8")) > MAX_SUBPROCESS_OUTPUT_BYTES
            or len(completed.stderr.encode("utf-8")) > MAX_SUBPROCESS_OUTPUT_BYTES
        ):
            raise SchedulerError(
                "CHILD_OUTPUT_EXCEEDED_POLICY", effects_may_have_started=True
            )
        if completed.returncode != 0:
            raise SchedulerError("CHILD_BACKUP_FAILED", effects_may_have_started=True)
        child = _validate_child_receipt(
            receipt_path=child_receipt_path,
            backup_dir=backup_dir,
            revision=revision,
            retention_days=retention_days,
            schedule_slot=slot,
            interval_seconds=interval_seconds,
            observed_at=observed_at,
        )
        return _result(
            status="PASS",
            revision=revision,
            slot=slot,
            interval_seconds=interval_seconds,
            retention_days=retention_days,
            backup_invoked=True,
            idempotent_replay=False,
            child=child,
        )


def _encode_public(payload: dict[str, Any]) -> bytes:
    scheduled_fields = {
        "schema_version",
        "receipt_type",
        "tool_version",
        "status",
        "repository_revision",
        "schedule_slot_utc",
        "interval_seconds",
        "retention_days",
        "backup_invoked",
        "idempotent_replay",
        "overlap_detected",
        "child_receipt_sha256",
        "backup_manifest_sha256",
        "backup_completed_at_utc",
        "retention_not_before_utc",
        "object_count",
        "encrypted_envelope_count",
        "source_mutated",
        "credential_values_retained",
        "raw_paths_retained",
        "release_gate_eligible",
    }
    terminal_fields = {
        "schema_version",
        "receipt_type",
        "status",
        "error_code",
        "overlap_detected",
        "effects_may_have_started",
        "credential_values_retained",
        "raw_paths_retained",
        "release_gate_eligible",
    }
    expected = (
        scheduled_fields
        if payload.get("receipt_type") == "automatic-backup-schedule"
        else terminal_fields
    )
    if set(payload) != expected:
        raise SchedulerError("PUBLIC_RECEIPT_SCHEMA_DRIFT")
    forbidden = ("password", "secret", "credential", "endpoint", "database_url")
    for key, value in payload.items():
        lowered = key.lower()
        if lowered not in {"credential_values_retained"} and any(
            item in lowered for item in forbidden
        ):
            raise SchedulerError("PUBLIC_RECEIPT_FORBIDDEN_FIELD")
        if isinstance(value, str) and ("/" in value or "@" in value):
            if key not in {
                "schedule_slot_utc",
                "backup_completed_at_utc",
                "retention_not_before_utc",
            }:
                raise SchedulerError("PUBLIC_RECEIPT_FORBIDDEN_VALUE")
    raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if len(raw) > MAX_JSON_BYTES:
        raise SchedulerError("PUBLIC_RECEIPT_TOO_LARGE")
    return raw


def _reserve_output(path: Path) -> int:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise SchedulerError("OUTPUT_RESERVATION_FAILED") from exc
    return descriptor


def _write_reserved(descriptor: int, payload: dict[str, Any]) -> None:
    try:
        raw = _encode_public(payload)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise SchedulerError("OUTPUT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--interval-seconds", type=int, default=21_600)
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--postgres-container", required=True)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--minio-endpoint-env", required=True)
    parser.add_argument("--minio-access-key-env", required=True)
    parser.add_argument("--minio-secret-key-env", required=True)
    parser.add_argument("--minio-bucket-env", required=True)
    parser.add_argument("--encryption-key-env", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        descriptor = _reserve_output(args.json_output)
    except SchedulerError:
        print("automatic backup output admission failed closed", file=sys.stderr)
        return 2
    try:
        result = run_schedule(
            repo=args.repo,
            backup_root=args.backup_root,
            expected_revision=args.expected_revision,
            interval_seconds=args.interval_seconds,
            retention_days=args.retention_days,
            postgres_container=args.postgres_container,
            database_url_env=args.database_url_env,
            minio_endpoint_env=args.minio_endpoint_env,
            minio_access_key_env=args.minio_access_key_env,
            minio_secret_key_env=args.minio_secret_key_env,
            minio_bucket_env=args.minio_bucket_env,
            encryption_key_env=args.encryption_key_env,
            dry_run=args.dry_run,
            timeout_seconds=args.timeout_seconds,
        )
        exit_code = 0 if result["status"] in {"PASS", "SKIPPED", "DRY_RUN"} else 2
    except SchedulerError as exc:
        result = {
            "schema_version": "1.0",
            "receipt_type": "automatic-backup-terminal",
            "status": "FAIL",
            "error_code": exc.code,
            "overlap_detected": exc.code == "OVERLAP_ACTIVE",
            "effects_may_have_started": exc.effects_may_have_started,
            "credential_values_retained": False,
            "raw_paths_retained": False,
            "release_gate_eligible": False,
        }
        exit_code = 2
    except Exception:
        result = {
            "schema_version": "1.0",
            "receipt_type": "automatic-backup-terminal",
            "status": "FAIL",
            "error_code": "INTERNAL_ERROR",
            "overlap_detected": False,
            "effects_may_have_started": True,
            "credential_values_retained": False,
            "raw_paths_retained": False,
            "release_gate_eligible": False,
        }
        exit_code = 3
    try:
        _write_reserved(descriptor, result)
    except Exception:
        print("automatic backup terminal receipt write failed", file=sys.stderr)
        return 3
    if exit_code:
        print(
            "automatic backup failed closed; inspect terminal receipt", file=sys.stderr
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
