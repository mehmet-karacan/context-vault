#!/usr/bin/env python3
"""A11 backup policy, fresh-target restore and reconciliation primitives.

Dry-run produces a bounded plan and never touches a database or object store.
Effectful backup/restore modes accept credentials only through named environment
variables and restore only to a separately provisioned, verified-empty endpoint.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit


TOOL_VERSION = "2.0.0"
BACKUP_MANIFEST_SCHEMA = "2.0"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_OBJECTS = 100_000
MAX_OBJECT_BYTES = 64 * 1024 * 1024 * 1024
MAX_TOTAL_OBJECT_BYTES = 1024 * 1024 * 1024 * 1024
MAX_KEY_BYTES = 1024
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_DB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")


class DrError(RuntimeError):
    """A backup or restore safety gate failed."""


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DrError("restore point timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise DrError("restore point timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise DrError("receipt timestamps must include timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_open(
    path: Path, *, max_bytes: int, expected_sha256: str | None = None
) -> tuple[BinaryIO, str, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DrError("backup material is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise DrError("backup material violates size or file-type policy")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            raise DrError(
                "backup material cannot be locked for stable reading"
            ) from exc
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        actual = digest.hexdigest()
        if not stable or (expected_sha256 is not None and actual != expected_sha256):
            raise DrError("backup material binding mismatch")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return os.fdopen(descriptor, "rb", closefd=True), actual, before.st_size
    except Exception:
        os.close(descriptor)
        raise


def _stable_json_manifest(
    path: Path, expected_sha256: str
) -> tuple[dict[str, Any], str]:
    if not HEX_64.fullmatch(expected_sha256):
        raise DrError("expected manifest SHA-256 is malformed")
    stream, actual, _ = _stable_open(
        path, max_bytes=MAX_MANIFEST_BYTES, expected_sha256=expected_sha256
    )
    try:
        payload = stream.read(MAX_MANIFEST_BYTES + 1)
    finally:
        stream.close()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DrError("private backup manifest is malformed") from exc
    if not isinstance(value, dict):
        raise DrError("private backup manifest is malformed")
    return value, actual


def _resolved_endpoint(endpoint: str) -> tuple[frozenset[str], int]:
    value = endpoint if "://" in endpoint else f"http://{endpoint}"
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DrError("object endpoint is malformed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = frozenset(
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, port, type=socket.SOCK_STREAM
            )
        )
    except OSError as exc:
        raise DrError("object endpoint resolution failed") from exc
    if not addresses:
        raise DrError("object endpoint resolution failed")
    return addresses, port


def _valid_ip_addresses(values: list[Any]) -> bool:
    if values != sorted(set(values)):
        return False
    try:
        return all(str(ipaddress.ip_address(value)) == value for value in values)
    except ValueError:
        return False


def assert_distinct_object_hosts(source_endpoint: str, target_endpoint: str) -> None:
    source_addresses, source_port = _resolved_endpoint(source_endpoint)
    target_addresses, target_port = _resolved_endpoint(target_endpoint)
    if source_port == target_port and source_addresses & target_addresses:
        raise DrError("source and restore object hosts must be physically distinct")


def _database_identity(url: str) -> tuple[str, int, str]:
    parsed = urlsplit(url.replace("postgresql+psycopg2://", "postgresql://", 1))
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        raise DrError("database endpoint is malformed")
    database = parsed.path.lstrip("/")
    if not database:
        raise DrError("database name is missing")
    return parsed.hostname.lower(), parsed.port or 5432, database


def _run_text(argv: list[str], *, timeout: int = 30) -> str:
    try:
        completed = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_docker_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DrError("required local runtime inspection failed") from exc
    if len(completed.stdout) > 8192 or len(completed.stderr) > 8192:
        raise DrError("runtime inspection output exceeded policy")
    return completed.stdout.strip()


def _docker_env() -> dict[str, str]:
    allowed = {
        "PATH",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def _repository_revision(repo: Path, *, require_clean: bool) -> str:
    try:
        revision_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=_docker_env(),
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=_docker_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DrError("repository provenance inspection failed") from exc
    if (
        len(revision_result.stdout) > 128
        or len(revision_result.stderr) > 8192
        or len(status_result.stdout) > 1024 * 1024
        or len(status_result.stderr) > 8192
    ):
        raise DrError("repository provenance output exceeded policy")
    revision = revision_result.stdout.strip()
    if not HEX_40.fullmatch(revision):
        raise DrError("repository revision is malformed")
    if require_clean and status_result.stdout:
        raise DrError("effectful backup/restore requires a clean repository")
    return revision


def _container_id(container: str, *, require_restore_label: bool) -> str:
    if not container:
        raise DrError("an exact PostgreSQL container is required")
    identifier = _run_text(["docker", "inspect", "--format", "{{.Id}}", container])
    if not HEX_64.fullmatch(identifier):
        raise DrError("PostgreSQL container identity is malformed")
    running = _run_text(
        ["docker", "inspect", "--format", "{{.State.Running}}", identifier]
    )
    if running != "true":
        raise DrError("PostgreSQL container is not running")
    if require_restore_label:
        label = _run_text(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.context-vault.restore-target"}}',
                identifier,
            ]
        )
        if label != "true":
            raise DrError("restore PostgreSQL container lacks required target label")
    return identifier


def _postgres_container_identity(
    container_id: str, *, user: str, database: str | None
) -> dict[str, str]:
    if database is not None and not SAFE_DB_NAME.fullmatch(database):
        raise DrError("database name violates restore policy")
    selected_database = database or "postgres"
    output = _run_text(
        [
            "docker",
            "exec",
            container_id,
            "psql",
            "-X",
            "-U",
            user,
            "-d",
            selected_database,
            "-Atqc",
            "SELECT current_database() || E'\\t' || system_identifier::text "
            "FROM pg_control_system()",
        ]
    )
    parts = output.split("\t")
    if len(parts) != 2 or parts[0] != selected_database or not parts[1].isdigit():
        raise DrError("PostgreSQL runtime identity is malformed")
    return {
        "container_id": container_id,
        "database": selected_database,
        "system_identifier": parts[1],
    }


def _target_database_absent(container_id: str, *, user: str, database: str) -> bool:
    if not SAFE_DB_NAME.fullmatch(database):
        raise DrError("database name violates restore policy")
    output = _run_text(
        [
            "docker",
            "exec",
            container_id,
            "psql",
            "-X",
            "-U",
            user,
            "-d",
            "postgres",
            "-Atqc",
            f"SELECT EXISTS (SELECT FROM pg_database WHERE datname = '{database}')",
        ]
    )
    if output not in {"t", "f"}:
        raise DrError("target database admission result is malformed")
    return output == "f"


def _create_target_database(container_id: str, *, user: str, database: str) -> None:
    if not SAFE_DB_NAME.fullmatch(database):
        raise DrError("database name violates restore policy")
    _run_text(
        ["docker", "exec", container_id, "createdb", "-U", user, "--", database],
        timeout=60,
    )


def reconcile_keys(*, db_keys: set[str], object_keys: set[str]) -> dict[str, Any]:
    missing = sorted(db_keys - object_keys)
    orphan = sorted(object_keys - db_keys)
    return {
        "db_reference_count": len(db_keys),
        "object_count": len(object_keys),
        "missing_object_count": len(missing),
        "orphan_object_count": len(orphan),
        "missing_object_key_hashes": [_hash(value) for value in missing],
        "orphan_object_key_hashes": [_hash(value) for value in orphan],
    }


def select_restore_point(
    points: list[dict[str, Any]], requested_at_utc: str
) -> dict[str, Any]:
    requested = _utc(requested_at_utc)
    candidates = [
        (point, _utc(str(point.get("completed_at_utc", "")))) for point in points
    ]
    eligible = [
        (point, timestamp) for point, timestamp in candidates if timestamp <= requested
    ]
    if not eligible:
        raise DrError("no completed backup exists at or before requested restore point")
    return max(eligible, key=lambda item: item[1])[0]


def evaluate_backup_policy(
    *,
    versioning_enabled: bool,
    encryption_envelopes: int,
    object_count: int,
    key_accessible: bool,
    retention_days: int,
) -> dict[str, Any]:
    values_valid = (
        object_count > 0
        and 0 <= encryption_envelopes <= object_count
        and 1 <= retention_days <= 3650
    )
    complete = (
        values_valid
        and versioning_enabled
        and encryption_envelopes == object_count
        and key_accessible
    )
    return {
        "status": "PASS" if complete else "FAIL",
        "versioning_enabled": versioning_enabled,
        "object_count": object_count,
        "encrypted_envelope_count": encryption_envelopes,
        "encryption_key_access_verified": key_accessible,
        "retention_days": retention_days,
        "raw_object_names_retained": False,
        "credential_values_retained": False,
    }


def restore_receipt(
    *,
    source_revision: str,
    backup_completed_at: datetime,
    restore_started_at: datetime,
    restore_finished_at: datetime,
    reconciliation: dict[str, Any],
    smoke: dict[str, bool],
    backup_manifest_sha256: str,
    executor_repository_revision: str,
    source_database_identity: dict[str, str],
    target_database_identity: dict[str, str],
    source_object_identity: tuple[frozenset[str], int],
    target_object_identity: tuple[frozenset[str], int],
) -> dict[str, Any]:
    required_smokes = {"migration", "auth", "retrieval", "citation"}
    if set(smoke) != required_smokes:
        raise DrError("restore smoke result set is incomplete")
    if (
        restore_started_at < backup_completed_at
        or restore_finished_at < restore_started_at
    ):
        raise DrError("restore timing order is invalid")
    if (
        not HEX_40.fullmatch(source_revision)
        or not HEX_40.fullmatch(executor_repository_revision)
        or not HEX_64.fullmatch(backup_manifest_sha256)
    ):
        raise DrError("restore receipt binding is malformed")
    reconciliation_fields = {
        "db_reference_count",
        "object_count",
        "missing_object_count",
        "orphan_object_count",
        "missing_object_key_hashes",
        "orphan_object_key_hashes",
    }
    if set(reconciliation) != reconciliation_fields:
        raise DrError("restore reconciliation result set is incomplete")
    for field in (
        "db_reference_count",
        "object_count",
        "missing_object_count",
        "orphan_object_count",
    ):
        value = reconciliation[field]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= MAX_OBJECTS
        ):
            raise DrError("restore reconciliation count is malformed")
    for field in ("missing_object_key_hashes", "orphan_object_key_hashes"):
        values = reconciliation[field]
        if (
            not isinstance(values, list)
            or len(values) > MAX_OBJECTS
            or not all(
                isinstance(value, str) and HEX_64.fullmatch(value) for value in values
            )
        ):
            raise DrError("restore reconciliation hashes are malformed")
    reconciled = (
        reconciliation.get("missing_object_count") == 0
        and reconciliation.get("orphan_object_count") == 0
    )
    passed = reconciled and all(smoke.values())
    result = {
        "schema_version": "1.0",
        "receipt_type": "fresh-target-restore-drill",
        "tool_version": TOOL_VERSION,
        "status": "PASS" if passed else "FAIL",
        "source_repository_revision": source_revision,
        "executor_repository_revision": executor_repository_revision,
        "backup_manifest_sha256": backup_manifest_sha256,
        "backup_completed_at_utc": _utc_text(backup_completed_at),
        "restore_started_at_utc": _utc_text(restore_started_at),
        "restore_finished_at_utc": _utc_text(restore_finished_at),
        "rpo_seconds": round(
            (restore_started_at - backup_completed_at).total_seconds(), 3
        ),
        "rto_seconds": round(
            (restore_finished_at - restore_started_at).total_seconds(), 3
        ),
        "reconciliation": reconciliation,
        "smoke": smoke,
        "fresh_target_verified": True,
        "source_mutated": False,
        "raw_object_names_retained": False,
        "credential_values_retained": False,
        "release_gate_eligible": False,
        "human_release_decision_required": True,
    }
    result["database_binding"] = {
        "source_container_id_sha256": _hash(source_database_identity["container_id"]),
        "source_system_identifier_sha256": _hash(
            source_database_identity["system_identifier"]
        ),
        "target_container_id_sha256": _hash(target_database_identity["container_id"]),
        "target_system_identifier_sha256": _hash(
            target_database_identity["system_identifier"]
        ),
        "target_database_sha256": _hash(target_database_identity["database"]),
    }
    result["object_host_binding"] = {
        "source_resolved_identity_sha256": _hash(
            json.dumps(
                [sorted(source_object_identity[0]), source_object_identity[1]],
                separators=(",", ":"),
            )
        ),
        "target_resolved_identity_sha256": _hash(
            json.dumps(
                [sorted(target_object_identity[0]), target_object_identity[1]],
                separators=(",", ":"),
            )
        ),
    }
    return result


def _minio_client(endpoint: str, access_key: str, secret_key: str):
    try:
        from minio import Minio
    except ImportError as exc:
        raise DrError("MinIO SDK is unavailable") from exc
    value = endpoint if "://" in endpoint else f"http://{endpoint}"
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise DrError("MinIO endpoint is malformed")
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return Minio(
        host,
        access_key=access_key,
        secret_key=secret_key,
        secure=parsed.scheme == "https",
    )


def _decode_encryption_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise DrError("object encryption key is malformed") from exc
    if len(decoded) != 32:
        raise DrError("object encryption key is malformed")
    return decoded


def _authenticate_envelope(
    stream: BinaryIO, *, object_key: str, encryption_key: bytes, size: int
) -> None:
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise DrError("AES-GCM verification support is unavailable") from exc
    if size < 7 + 12 + 16:
        raise DrError("encrypted object envelope is malformed")
    stream.seek(0)
    if stream.read(7) != b"CVENC1\x00":
        raise DrError("encrypted object envelope is malformed")
    nonce = stream.read(12)
    ciphertext_bytes = size - 7 - 12 - 16
    stream.seek(size - 16)
    tag = stream.read(16)
    if len(nonce) != 12 or len(tag) != 16:
        raise DrError("encrypted object envelope is malformed")
    decryptor = Cipher(
        algorithms.AES(encryption_key), modes.GCM(nonce, tag)
    ).decryptor()
    decryptor.authenticate_additional_data(object_key.encode("utf-8"))
    stream.seek(7 + 12)
    remaining = ciphertext_bytes
    try:
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise DrError("encrypted object envelope is truncated")
            decryptor.update(chunk)
            remaining -= len(chunk)
        decryptor.finalize()
    except (InvalidTag, ValueError) as exc:
        raise DrError("encrypted object authentication failed") from exc
    finally:
        stream.seek(0)


def _dump_database(
    *, database_url: str, destination: Path, postgres_container_id: str
) -> None:
    _, _, database = _database_identity(database_url)
    parsed = urlsplit(
        database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    )
    user = parsed.username
    if not user:
        raise DrError("database user is missing")
    argv = [
        "docker",
        "exec",
        "-i",
        postgres_container_id,
        "pg_dump",
        "-U",
        user,
        "-d",
        database,
        "-Fc",
        "--no-owner",
        "--no-privileges",
    ]
    env = _docker_env()
    with destination.open("xb") as stream:
        os.chmod(destination, 0o600)
        try:
            subprocess.run(
                argv,
                check=True,
                stdout=stream,
                stderr=subprocess.PIPE,
                env=env,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            destination.unlink(missing_ok=True)
            raise DrError("PostgreSQL backup command failed") from exc
    if destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise DrError("PostgreSQL backup is empty")


def create_backup(
    *,
    repo: Path,
    output_dir: Path,
    database_url: str,
    postgres_container: str | None,
    minio_endpoint: str,
    minio_access_key: str,
    minio_secret_key: str,
    minio_bucket: str,
    encryption_key: str,
    retention_days: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a private DB/object snapshot and a redacted public-safe summary."""

    if output_dir.exists() or not 1 <= retention_days <= 3650:
        raise DrError("backup destination must be new and retention bounded")
    revision = _repository_revision(repo, require_clean=True)
    decoded_encryption_key = _decode_encryption_key(encryption_key)
    parsed_database = urlsplit(
        database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    )
    if not parsed_database.username:
        raise DrError("database user is missing")
    _, _, database = _database_identity(database_url)
    source_container_id = _container_id(
        postgres_container or "", require_restore_label=False
    )
    source_database_identity = _postgres_container_identity(
        source_container_id, user=parsed_database.username, database=database
    )
    source_addresses, source_port = _resolved_endpoint(minio_endpoint)
    source_object_identity = {
        "resolved_addresses": sorted(source_addresses),
        "port": source_port,
        "bucket": minio_bucket,
    }
    output_dir.mkdir(parents=True, mode=0o700)
    objects_dir = output_dir / "objects"
    objects_dir.mkdir(mode=0o700)
    started = datetime.now(timezone.utc)
    try:
        database_dump = output_dir / "postgres.dump"
        _dump_database(
            database_url=database_url,
            destination=database_dump,
            postgres_container_id=source_container_id,
        )
        client = _minio_client(minio_endpoint, minio_access_key, minio_secret_key)
        if not client.bucket_exists(minio_bucket):
            raise DrError("source object bucket is unavailable")
        versioning = client.get_bucket_versioning(minio_bucket)
        versioning_enabled = getattr(versioning, "status", None) == "Enabled"
        inventory: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        encrypted_count = 0
        total_bytes = 0
        for item in client.list_objects(minio_bucket, recursive=True):
            key = item.object_name
            if (
                len(inventory) >= MAX_OBJECTS
                or not isinstance(key, str)
                or not key
                or len(key.encode("utf-8")) > MAX_KEY_BYTES
                or key in seen_keys
            ):
                raise DrError("object inventory contains an invalid key")
            seen_keys.add(key)
            key_hash = _hash(key)
            target = objects_dir / key_hash
            response = client.get_object(minio_bucket, key)
            digest = hashlib.sha256()
            prefix = b""
            size = 0
            try:
                with target.open("xb") as stream:
                    os.chmod(target, 0o600)
                    while chunk := response.read(1024 * 1024):
                        if len(prefix) < 7:
                            prefix += chunk[: 7 - len(prefix)]
                        stream.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                        if size > MAX_OBJECT_BYTES:
                            raise DrError("object exceeds backup byte policy")
            finally:
                response.close()
                response.release_conn()
            encrypted = prefix == b"CVENC1\x00"
            if encrypted:
                with target.open("rb") as verification_stream:
                    _authenticate_envelope(
                        verification_stream,
                        object_key=key,
                        encryption_key=decoded_encryption_key,
                        size=size,
                    )
            encrypted_count += int(encrypted)
            total_bytes += size
            if total_bytes > MAX_TOTAL_OBJECT_BYTES:
                raise DrError("object backup exceeds total byte policy")
            inventory.append(
                {
                    "key": key,
                    "key_sha256": key_hash,
                    "payload_sha256": digest.hexdigest(),
                    "bytes": size,
                    "encrypted_envelope": encrypted,
                }
            )
        inventory.sort(key=lambda item: item["key"])
        policy = evaluate_backup_policy(
            versioning_enabled=versioning_enabled,
            encryption_envelopes=encrypted_count,
            object_count=len(inventory),
            key_accessible=True,
            retention_days=retention_days,
        )
        if policy["status"] != "PASS":
            raise DrError("backup policy validation failed")
        completed = datetime.now(timezone.utc)
        private_manifest = {
            "schema_version": BACKUP_MANIFEST_SCHEMA,
            "backup_id": f"cv-{completed.strftime('%Y%m%dT%H%M%SZ')}",
            "repository_revision": revision,
            "source_database_identity": source_database_identity,
            "source_object_identity": source_object_identity,
            "started_at_utc": _utc_text(started),
            "completed_at_utc": _utc_text(completed),
            "retention_days": retention_days,
            "postgres_dump_file": database_dump.name,
            "postgres_dump_sha256": _sha_file(database_dump),
            "object_inventory": inventory,
            "object_inventory_digest": hashlib.sha256(
                json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "object_count": len(inventory),
            "object_bytes": total_bytes,
            "versioning_enabled": versioning_enabled,
            "encrypted_envelope_count": encrypted_count,
            "raw_object_names_retained": True,
            "credential_values_retained": False,
        }
        manifest_path = output_dir / "backup-manifest.private.json"
        _write_new(manifest_path, private_manifest)
        public = {
            "schema_version": "1.0",
            "receipt_type": "backup-complete",
            "status": "PASS",
            "repository_revision": revision,
            "backup_id_hash": _hash(private_manifest["backup_id"]),
            "backup_manifest_sha256": _sha_file(manifest_path),
            "source_database_binding": {
                "container_id_sha256": _hash(source_container_id),
                "system_identifier_sha256": _hash(
                    source_database_identity["system_identifier"]
                ),
                "database_sha256": _hash(database),
            },
            "source_object_binding": {
                "resolved_identity_sha256": _hash(
                    json.dumps(
                        [sorted(source_addresses), source_port], separators=(",", ":")
                    )
                ),
                "bucket_sha256": _hash(minio_bucket),
            },
            "started_at_utc": private_manifest["started_at_utc"],
            "completed_at_utc": private_manifest["completed_at_utc"],
            "retention_days": retention_days,
            "postgres_dump_sha256": private_manifest["postgres_dump_sha256"],
            "object_inventory_digest": private_manifest["object_inventory_digest"],
            "object_count": len(inventory),
            "object_bytes": total_bytes,
            "versioning_enabled": versioning_enabled,
            "encrypted_envelope_count": encrypted_count,
            "source_mutated": False,
            "raw_object_names_retained_in_receipt": False,
            "private_manifest_contains_restore_keys": True,
            "credential_values_retained": False,
            "restore_verified": False,
            "release_gate_eligible": False,
        }
        return private_manifest, public
    except Exception:
        # Keep partial private material for operator diagnosis; it is never claimed
        # as a completed backup and remains under the mode-0700 directory.
        raise


def _connect(database_url: str):
    try:
        import psycopg2

        return psycopg2.connect(database_url, connect_timeout=5)
    except Exception as exc:
        raise DrError("database connection is unavailable") from exc


def _url_with_database(database_url: str, database: str) -> str:
    parsed = urlsplit(
        database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    )
    return parsed._replace(path=f"/{database}").geturl()


def _network_cluster_identity(database_url: str) -> str:
    connection = _connect(_url_with_database(database_url, "postgres"))
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
            value = str(cursor.fetchone()[0])
    except Exception as exc:
        raise DrError("target database network identity check failed") from exc
    finally:
        connection.close()
    if not value.isdigit():
        raise DrError("target database network identity is malformed")
    return value


def _restore_database(
    *, database_url: str, source: BinaryIO, postgres_container_id: str
) -> None:
    parsed = urlsplit(
        database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    )
    _, _, database = _database_identity(database_url)
    if not parsed.username:
        raise DrError("restore database user is missing")
    argv = [
        "docker",
        "exec",
        "-i",
        postgres_container_id,
        "pg_restore",
        "-U",
        parsed.username,
        "-d",
        database,
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
    ]
    source.seek(0)
    try:
        subprocess.run(
            argv,
            stdin=source,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_docker_env(),
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DrError("PostgreSQL restore command failed") from exc


def _database_keys_and_smoke(
    database_url: str, expected_head: str
) -> tuple[set[str], dict[str, bool]]:
    connection = _connect(database_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            migration = cursor.fetchone()[0] == expected_head
            cursor.execute(
                "SELECT storage_key FROM storage_objects "
                "WHERE status <> 'deleted' AND storage_key NOT LIKE 'inline:%'"
            )
            rows = cursor.fetchmany(MAX_OBJECTS + 1)
            if len(rows) > MAX_OBJECTS:
                raise DrError("restored database object reference count exceeds policy")
            keys = {row[0] for row in rows if row[0]}
            cursor.execute(
                "SELECT to_regclass('public.api_keys') IS NOT NULL "
                "AND to_regclass('public.principals') IS NOT NULL"
            )
            auth = bool(cursor.fetchone()[0])
            cursor.execute(
                "SELECT count(*)=0 FROM chunk_embeddings ce "
                "LEFT JOIN embedding_profiles ep ON ep.id=ce.embedding_profile_id "
                "WHERE ep.id IS NULL"
            )
            retrieval = bool(cursor.fetchone()[0])
            cursor.execute(
                "SELECT count(*)=0 FROM message_citations "
                "WHERE validation_result='valid' "
                "AND (evidence_hash IS NULL OR citation_label IS NULL)"
            )
            citation = bool(cursor.fetchone()[0])
    except Exception as exc:
        raise DrError("restored database smoke failed") from exc
    finally:
        connection.close()
    return keys, {
        "migration": migration,
        "auth": auth,
        "retrieval": retrieval,
        "citation": citation,
    }


def _validate_backup_materials(
    backup_dir: Path, manifest: dict[str, Any], *, encryption_key: bytes
) -> tuple[BinaryIO, list[tuple[dict[str, Any], BinaryIO]]]:
    """Validate the complete private restore set before touching a target."""

    required = {
        "schema_version",
        "backup_id",
        "repository_revision",
        "source_database_identity",
        "source_object_identity",
        "started_at_utc",
        "completed_at_utc",
        "retention_days",
        "postgres_dump_file",
        "postgres_dump_sha256",
        "object_inventory",
        "object_inventory_digest",
        "object_count",
        "object_bytes",
        "versioning_enabled",
        "encrypted_envelope_count",
        "raw_object_names_retained",
        "credential_values_retained",
    }
    if (
        set(manifest) != required
        or manifest.get("schema_version") != BACKUP_MANIFEST_SCHEMA
    ):
        raise DrError("private backup manifest is incomplete")
    revision = manifest["repository_revision"]
    if not isinstance(revision, str) or not HEX_40.fullmatch(revision):
        raise DrError("private backup repository binding is malformed")
    source_identity = manifest["source_database_identity"]
    if (
        not isinstance(source_identity, dict)
        or set(source_identity) != {"container_id", "database", "system_identifier"}
        or not HEX_64.fullmatch(str(source_identity.get("container_id", "")))
        or not SAFE_DB_NAME.fullmatch(str(source_identity.get("database", "")))
        or not str(source_identity.get("system_identifier", "")).isdigit()
    ):
        raise DrError("source database identity binding is malformed")
    source_object_identity = manifest["source_object_identity"]
    if (
        not isinstance(source_object_identity, dict)
        or set(source_object_identity) != {"resolved_addresses", "port", "bucket"}
        or not isinstance(source_object_identity["resolved_addresses"], list)
        or not source_object_identity["resolved_addresses"]
        or not _valid_ip_addresses(source_object_identity["resolved_addresses"])
        or not isinstance(source_object_identity["port"], int)
        or isinstance(source_object_identity["port"], bool)
        or not 1 <= source_object_identity["port"] <= 65535
        or not isinstance(source_object_identity["bucket"], str)
        or not source_object_identity["bucket"]
    ):
        raise DrError("source object identity binding is malformed")
    started_at = _utc(str(manifest["started_at_utc"]))
    completed_at = _utc(str(manifest["completed_at_utc"]))
    if completed_at < started_at:
        raise DrError("private backup timestamp order is invalid")
    if (
        not isinstance(manifest["retention_days"], int)
        or isinstance(manifest["retention_days"], bool)
        or not 1 <= manifest["retention_days"] <= 3650
        or manifest["versioning_enabled"] is not True
        or manifest["raw_object_names_retained"] is not True
        or manifest["credential_values_retained"] is not False
    ):
        raise DrError("private backup policy binding is malformed")
    dump_name = manifest["postgres_dump_file"]
    if not isinstance(dump_name, str) or Path(dump_name).name != dump_name:
        raise DrError("PostgreSQL backup path is unsafe")
    dump_hash = manifest["postgres_dump_sha256"]
    if not isinstance(dump_hash, str) or not HEX_64.fullmatch(dump_hash):
        raise DrError("PostgreSQL backup hash is malformed")
    dump, _, _ = _stable_open(
        backup_dir / dump_name,
        max_bytes=MAX_TOTAL_OBJECT_BYTES,
        expected_sha256=dump_hash,
    )
    inventory = manifest["object_inventory"]
    if not isinstance(inventory, list) or not inventory or len(inventory) > MAX_OBJECTS:
        dump.close()
        raise DrError("object inventory is malformed")
    digest = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        not isinstance(manifest["object_inventory_digest"], str)
        or not HEX_64.fullmatch(manifest["object_inventory_digest"])
        or digest != manifest["object_inventory_digest"]
    ):
        dump.close()
        raise DrError("object inventory digest mismatch")
    prepared: list[tuple[dict[str, Any], BinaryIO]] = []
    seen_keys: set[str] = set()
    total_bytes = 0
    try:
        for item in inventory:
            if not isinstance(item, dict) or set(item) != {
                "key",
                "key_sha256",
                "payload_sha256",
                "bytes",
                "encrypted_envelope",
            }:
                raise DrError("object inventory entry is malformed")
            key = item.get("key")
            key_hash = item.get("key_sha256")
            payload_hash = item.get("payload_sha256")
            size = item.get("bytes")
            if (
                not isinstance(key, str)
                or not key
                or len(key.encode("utf-8")) > MAX_KEY_BYTES
                or key in seen_keys
                or not isinstance(key_hash, str)
                or key_hash != _hash(key)
                or not isinstance(payload_hash, str)
                or not HEX_64.fullmatch(payload_hash)
                or not isinstance(size, int)
                or not 7 <= size <= MAX_OBJECT_BYTES
                or item.get("encrypted_envelope") is not True
            ):
                raise DrError("object inventory entry is malformed")
            seen_keys.add(key)
            stream, _, actual_size = _stable_open(
                backup_dir / "objects" / key_hash,
                max_bytes=MAX_OBJECT_BYTES,
                expected_sha256=payload_hash,
            )
            prefix = stream.read(7)
            stream.seek(0)
            if actual_size != size or prefix != b"CVENC1\x00":
                stream.close()
                raise DrError("object backup encryption or size binding mismatch")
            _authenticate_envelope(
                stream,
                object_key=key,
                encryption_key=encryption_key,
                size=actual_size,
            )
            total_bytes += size
            if total_bytes > MAX_TOTAL_OBJECT_BYTES:
                stream.close()
                raise DrError("object backup exceeds total byte policy")
            prepared.append((item, stream))
        if (
            not isinstance(manifest["object_count"], int)
            or isinstance(manifest["object_count"], bool)
            or not isinstance(manifest["object_bytes"], int)
            or isinstance(manifest["object_bytes"], bool)
            or not isinstance(manifest["encrypted_envelope_count"], int)
            or isinstance(manifest["encrypted_envelope_count"], bool)
            or manifest["object_count"] < 0
            or manifest["object_bytes"] < 0
            or manifest["encrypted_envelope_count"] < 0
            or manifest["object_count"] != len(inventory)
            or manifest["object_bytes"] != total_bytes
            or manifest["encrypted_envelope_count"] != len(inventory)
        ):
            raise DrError("object inventory aggregate binding mismatch")
        return dump, prepared
    except Exception:
        dump.close()
        for _, stream in prepared:
            stream.close()
        raise


def run_restore_drill(
    *,
    repo: Path,
    backup_dir: Path,
    target_database_url: str,
    target_postgres_container: str | None,
    source_minio_endpoint: str,
    target_minio_endpoint: str,
    source_bucket: str,
    target_bucket: str,
    target_minio_access_key: str,
    target_minio_secret_key: str,
    expected_head: str,
    expected_manifest_sha256: str,
    encryption_key: str,
) -> dict[str, Any]:
    executor_revision = _repository_revision(repo, require_clean=True)
    manifest_path = backup_dir / "backup-manifest.private.json"
    try:
        from minio.versioningconfig import ENABLED, VersioningConfig
    except ImportError as exc:
        raise DrError("MinIO versioning support is unavailable") from exc

    manifest, actual_manifest_sha256 = _stable_json_manifest(
        manifest_path, expected_manifest_sha256
    )
    dump, prepared_objects = _validate_backup_materials(
        backup_dir, manifest, encryption_key=_decode_encryption_key(encryption_key)
    )
    try:
        parsed_target = urlsplit(
            target_database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
        )
        if not parsed_target.username:
            raise DrError("target database user is missing")
        _, _, target_database = _database_identity(target_database_url)
        target_container_id = _container_id(
            target_postgres_container or "", require_restore_label=True
        )
        target_cluster = _postgres_container_identity(
            target_container_id, user=parsed_target.username, database=None
        )
        source_database_identity = manifest["source_database_identity"]
        if (
            source_database_identity["container_id"] == target_container_id
            or source_database_identity["system_identifier"]
            == target_cluster["system_identifier"]
        ):
            raise DrError("source and restore PostgreSQL clusters must differ")
        if (
            _network_cluster_identity(target_database_url)
            != target_cluster["system_identifier"]
        ):
            raise DrError("target database URL does not bind to target container")
        if not _target_database_absent(
            target_container_id, user=parsed_target.username, database=target_database
        ):
            raise DrError("restore database target must not already exist")

        current_source_object_identity = _resolved_endpoint(source_minio_endpoint)
        bound_source_object_identity = manifest["source_object_identity"]
        source_object_identity = (
            frozenset(bound_source_object_identity["resolved_addresses"]),
            bound_source_object_identity["port"],
        )
        if (
            current_source_object_identity != source_object_identity
            or source_bucket != bound_source_object_identity["bucket"]
        ):
            raise DrError("source object endpoint does not match backup binding")
        target_object_identity = _resolved_endpoint(target_minio_endpoint)
        if (
            source_object_identity[1] == target_object_identity[1]
            and source_object_identity[0] & target_object_identity[0]
        ):
            raise DrError("source and restore object hosts must be physically distinct")
        client = _minio_client(
            target_minio_endpoint, target_minio_access_key, target_minio_secret_key
        )
        if client.bucket_exists(target_bucket):
            raise DrError("restore object bucket must not already exist")
        if (
            _resolved_endpoint(source_minio_endpoint) != source_object_identity
            or _resolved_endpoint(target_minio_endpoint) != target_object_identity
        ):
            raise DrError("object endpoint resolution changed during admission")

        # First effects happen only after every immutable identity and backup byte
        # has passed admission. The target DB and bucket are created by this tool.
        started = datetime.now(timezone.utc)
        _create_target_database(
            target_container_id, user=parsed_target.username, database=target_database
        )
        client.make_bucket(target_bucket)
        client.set_bucket_versioning(target_bucket, VersioningConfig(ENABLED))
        target_database_identity = _postgres_container_identity(
            target_container_id, user=parsed_target.username, database=target_database
        )
        _restore_database(
            database_url=target_database_url,
            source=dump,
            postgres_container_id=target_container_id,
        )
        for item, payload_stream in prepared_objects:
            payload_stream.seek(0)
            client.put_object(
                target_bucket,
                item["key"],
                payload_stream,
                length=item["bytes"],
                content_type="application/octet-stream",
                metadata={"context-vault-encryption": "aes-256-gcm-v1"},
            )
            response = client.get_object(target_bucket, item["key"])
            restored_digest = hashlib.sha256()
            try:
                while chunk := response.read(1024 * 1024):
                    restored_digest.update(chunk)
            finally:
                response.close()
                response.release_conn()
            if restored_digest.hexdigest() != item["payload_sha256"]:
                raise DrError("restored object payload hash mismatch")
        db_keys, smoke = _database_keys_and_smoke(target_database_url, expected_head)
        object_keys = {
            item.object_name
            for item in client.list_objects(target_bucket, recursive=True)
        }
        reconciliation = reconcile_keys(db_keys=db_keys, object_keys=object_keys)
        finished = datetime.now(timezone.utc)
        return restore_receipt(
            source_revision=manifest["repository_revision"],
            backup_completed_at=_utc(manifest["completed_at_utc"]),
            restore_started_at=started,
            restore_finished_at=finished,
            reconciliation=reconciliation,
            smoke=smoke,
            backup_manifest_sha256=actual_manifest_sha256,
            executor_repository_revision=executor_revision,
            source_database_identity=source_database_identity,
            target_database_identity=target_database_identity,
            source_object_identity=source_object_identity,
            target_object_identity=target_object_identity,
        )
    finally:
        dump.close()
        for _, stream in prepared_objects:
            stream.close()


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise DrError("private manifest exceeds size policy")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise DrError("output must be a new private regular file") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise DrError("output mode is not 0600")
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _encode_public_json(payload: dict[str, Any]) -> bytes:
    schemas = {
        "backup-complete": {
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
        },
        "fresh-target-restore-drill": {
            "schema_version",
            "receipt_type",
            "tool_version",
            "status",
            "source_repository_revision",
            "executor_repository_revision",
            "backup_manifest_sha256",
            "backup_completed_at_utc",
            "restore_started_at_utc",
            "restore_finished_at_utc",
            "rpo_seconds",
            "rto_seconds",
            "reconciliation",
            "smoke",
            "fresh_target_verified",
            "source_mutated",
            "raw_object_names_retained",
            "credential_values_retained",
            "release_gate_eligible",
            "human_release_decision_required",
            "database_binding",
            "object_host_binding",
        },
        "backup-restore-dry-run": {
            "schema_version",
            "receipt_type",
            "status",
            "tool_version",
            "repository_revision",
            "retention_days",
            "planned_steps",
            "effects_performed",
            "credential_values_read",
        },
        "backup-restore-terminal": {
            "schema_version",
            "receipt_type",
            "status",
            "error_code",
            "effects_may_have_started",
            "credential_values_retained",
        },
    }
    receipt_type = payload.get("receipt_type")
    if receipt_type not in schemas or set(payload) != schemas[receipt_type]:
        raise DrError("public receipt schema is not allowlisted")
    if receipt_type == "fresh-target-restore-drill":
        if set(payload["smoke"]) != {"migration", "auth", "retrieval", "citation"}:
            raise DrError("public receipt smoke schema is not allowlisted")
        if set(payload["reconciliation"]) != {
            "db_reference_count",
            "object_count",
            "missing_object_count",
            "orphan_object_count",
            "missing_object_key_hashes",
            "orphan_object_key_hashes",
        }:
            raise DrError("public receipt reconciliation schema is not allowlisted")
        if set(payload["database_binding"]) != {
            "source_container_id_sha256",
            "source_system_identifier_sha256",
            "target_container_id_sha256",
            "target_system_identifier_sha256",
            "target_database_sha256",
        } or set(payload["object_host_binding"]) != {
            "source_resolved_identity_sha256",
            "target_resolved_identity_sha256",
        }:
            raise DrError("public receipt identity schema is not allowlisted")
    if receipt_type == "backup-complete":
        if set(payload["source_database_binding"]) != {
            "container_id_sha256",
            "system_identifier_sha256",
            "database_sha256",
        } or set(payload["source_object_binding"]) != {
            "resolved_identity_sha256",
            "bucket_sha256",
        }:
            raise DrError("public receipt identity schema is not allowlisted")
    forbidden_keys = {
        "key",
        "object_name",
        "database_url",
        "endpoint",
        "password",
        "secret",
        "token",
        "credential",
    }

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered = str(key).lower()
                if lowered in forbidden_keys or lowered.endswith("_path"):
                    raise DrError("public receipt contains a forbidden field")
                inspect(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect(nested)
        elif isinstance(value, str):
            if len(value) > 4096 or ("://" in value and "@" in value):
                raise DrError("public receipt contains a forbidden value")

    inspect(payload)
    encoded = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
    if len(encoded) > 1024 * 1024:
        raise DrError("public receipt exceeds size policy")
    return encoded


def _reserve_output(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise DrError("output receipt cannot be reserved") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise DrError("output receipt must be a regular file")
    return descriptor


def _write_reserved(descriptor: int, payload: dict[str, Any]) -> None:
    encoded = _encode_public_json(payload)
    view = memoryview(encoded)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise DrError("terminal receipt write failed")
        view = view[written:]
    os.fsync(descriptor)
    os.close(descriptor)


def dry_run_plan(repo: Path, retention_days: int) -> dict[str, Any]:
    if not 1 <= retention_days <= 3650:
        raise DrError("retention must be between 1 and 3650 days")
    revision = _repository_revision(repo, require_clean=False)
    return {
        "schema_version": "1.0",
        "receipt_type": "backup-restore-dry-run",
        "status": "DRY_RUN",
        "tool_version": TOOL_VERSION,
        "repository_revision": revision,
        "retention_days": retention_days,
        "planned_steps": [
            "bind-source-container-cluster-and-object-host-identities",
            "verify-versioning-and-authenticate-every-encryption-envelope",
            "create-postgresql-custom-format-backup",
            "create-private-object-inventory-and-payload-backup",
            "select-restore-point-and-external-manifest-sha256",
            "verify-labeled-distinct-targets-do-not-exist",
            "reserve-terminal-receipt-before-effects",
            "create-target-database-and-bucket",
            "restore-database-and-objects",
            "run-migration-auth-retrieval-citation-smoke",
            "reconcile-database-object-references",
            "emit-rpo-rto-receipt",
        ],
        "effects_performed": False,
        "credential_values_read": False,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--restore-drill", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--database-url-env")
    parser.add_argument("--postgres-container")
    parser.add_argument("--minio-endpoint-env")
    parser.add_argument("--minio-access-key-env")
    parser.add_argument("--minio-secret-key-env")
    parser.add_argument("--minio-bucket-env")
    parser.add_argument("--encryption-key-env")
    parser.add_argument("--target-database-url-env")
    parser.add_argument("--target-postgres-container")
    parser.add_argument("--target-minio-endpoint-env")
    parser.add_argument("--target-minio-access-key-env")
    parser.add_argument("--target-minio-secret-key-env")
    parser.add_argument("--target-minio-bucket-env")
    parser.add_argument("--expected-head", default="cv3_00000007")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args(argv)
    if sum((args.dry_run, args.backup, args.restore_drill)) != 1:
        parser.error("choose exactly one of --dry-run, --backup or --restore-drill")
    return args


def _required_env(name: str | None) -> str:
    if not name or not os.environ.get(name):
        raise DrError("a required named environment value is unavailable")
    return os.environ[name]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        receipt_fd = _reserve_output(args.json_output)
    except DrError:
        print("backup/restore admission failed closed", file=sys.stderr)
        return 2
    exit_code = 0
    try:
        if args.dry_run:
            result = dry_run_plan(args.repo.resolve(), args.retention_days)
        elif args.backup:
            if args.backup_dir is None:
                raise DrError("backup mode requires --backup-dir")
            _, result = create_backup(
                repo=args.repo.resolve(),
                output_dir=args.backup_dir.resolve(),
                database_url=_required_env(args.database_url_env),
                postgres_container=args.postgres_container,
                minio_endpoint=_required_env(args.minio_endpoint_env),
                minio_access_key=_required_env(args.minio_access_key_env),
                minio_secret_key=_required_env(args.minio_secret_key_env),
                minio_bucket=_required_env(args.minio_bucket_env),
                encryption_key=_required_env(args.encryption_key_env),
                retention_days=args.retention_days,
            )
        else:
            if args.backup_dir is None:
                raise DrError("restore mode requires --backup-dir")
            result = run_restore_drill(
                repo=args.repo.resolve(),
                backup_dir=args.backup_dir.resolve(),
                target_database_url=_required_env(args.target_database_url_env),
                target_postgres_container=args.target_postgres_container,
                source_minio_endpoint=_required_env(args.minio_endpoint_env),
                target_minio_endpoint=_required_env(args.target_minio_endpoint_env),
                source_bucket=_required_env(args.minio_bucket_env),
                target_bucket=_required_env(args.target_minio_bucket_env),
                target_minio_access_key=_required_env(args.target_minio_access_key_env),
                target_minio_secret_key=_required_env(args.target_minio_secret_key_env),
                expected_head=args.expected_head,
                expected_manifest_sha256=args.expected_manifest_sha256 or "",
                encryption_key=_required_env(args.encryption_key_env),
            )
        exit_code = 0 if result["status"] in {"PASS", "DRY_RUN"} else 2
    except DrError:
        result = {
            "schema_version": "1.0",
            "receipt_type": "backup-restore-terminal",
            "status": "FAIL",
            "error_code": "ADMISSION_FAILED",
            "effects_may_have_started": True,
            "credential_values_retained": False,
        }
        exit_code = 2
    except Exception:
        result = {
            "schema_version": "1.0",
            "receipt_type": "backup-restore-terminal",
            "status": "FAIL",
            "error_code": "INTERNAL_ERROR",
            "effects_may_have_started": True,
            "credential_values_retained": False,
        }
        exit_code = 3
    try:
        _write_reserved(receipt_fd, result)
    except Exception:
        try:
            os.close(receipt_fd)
        except OSError:
            pass
        print("backup/restore terminal receipt write failed", file=sys.stderr)
        return 3
    if exit_code:
        print("backup/restore failed closed; inspect terminal receipt", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
