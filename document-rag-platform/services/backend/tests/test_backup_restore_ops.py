"""A11 backup, reconciliation and fresh-target restore contracts."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts/backup_restore.py"
TEST_ENCRYPTION_KEY = bytes(range(32))
TEST_ENCRYPTION_KEY_B64 = base64.b64encode(TEST_ENCRYPTION_KEY).decode()


def _module():
    spec = importlib.util.spec_from_file_location("backup_restore", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _private_manifest(
    tmp_path: Path, *, object_key: str = "synthetic/object"
) -> tuple[dict[str, object], str]:
    module = _module()
    tmp_path.mkdir(parents=True, exist_ok=True)
    dump = tmp_path / "postgres.dump"
    dump.write_bytes(b"pgdump")
    objects = tmp_path / "objects"
    objects.mkdir()
    key = object_key
    nonce = bytes(range(12))
    payload = (
        b"CVENC1\x00"
        + nonce
        + AESGCM(TEST_ENCRYPTION_KEY).encrypt(nonce, b"payload", key.encode())
    )
    key_hash = module._hash(key)
    (objects / key_hash).write_bytes(payload)
    inventory = [
        {
            "key": key,
            "key_sha256": key_hash,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "encrypted_envelope": True,
        }
    ]
    manifest: dict[str, object] = {
        "schema_version": module.BACKUP_MANIFEST_SCHEMA,
        "backup_id": "cv-20260905T200000Z",
        "repository_revision": "a" * 40,
        "source_database_identity": {
            "container_id": "b" * 64,
            "database": "context",
            "system_identifier": "123456789",
        },
        "source_object_identity": {
            "resolved_addresses": ["192.0.2.10"],
            "port": 9000,
            "bucket": "source",
        },
        "started_at_utc": "2026-09-05T19:59:00Z",
        "completed_at_utc": "2026-09-05T20:00:00Z",
        "retention_days": 30,
        "postgres_dump_file": "postgres.dump",
        "postgres_dump_sha256": hashlib.sha256(b"pgdump").hexdigest(),
        "object_inventory": inventory,
        "object_inventory_digest": hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "object_count": 1,
        "object_bytes": len(payload),
        "versioning_enabled": True,
        "encrypted_envelope_count": 1,
        "raw_object_names_retained": True,
        "credential_values_retained": False,
    }
    encoded = json.dumps(manifest).encode()
    (tmp_path / "backup-manifest.private.json").write_bytes(encoded)
    return manifest, hashlib.sha256(encoded).hexdigest()


def test_source_and_target_object_hosts_must_be_physically_distinct(
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("192.0.2.10", 0))],
    )
    with pytest.raises(module.DrError, match="physically distinct"):
        module.assert_distinct_object_hosts(
            "http://source.invalid:9000", "https://target.invalid:9000"
        )


def test_reconciliation_projects_only_hashes() -> None:
    module = _module()
    result = module.reconcile_keys(
        db_keys={"projects/acme/private.pdf", "objects/shared"},
        object_keys={"projects/acme/private.pdf", "objects/orphan"},
    )
    rendered = json.dumps(result)
    assert result["missing_object_count"] == 1
    assert result["orphan_object_count"] == 1
    assert "private.pdf" not in rendered
    assert "objects/orphan" not in rendered


def test_restore_point_selection_is_bounded() -> None:
    module = _module()
    points = [
        {"backup_id": "a", "completed_at_utc": "2026-09-05T10:00:00Z"},
        {"backup_id": "b", "completed_at_utc": "2026-09-05T12:00:00Z"},
    ]
    selected = module.select_restore_point(points, "2026-09-05T11:00:00Z")
    assert selected["backup_id"] == "a"
    with pytest.raises(module.DrError):
        module.select_restore_point(points, "2026-09-05T09:00:00Z")


def test_backup_policy_requires_versioning_encryption_key_and_retention() -> None:
    module = _module()
    assert (
        module.evaluate_backup_policy(
            versioning_enabled=True,
            encryption_envelopes=4,
            object_count=4,
            key_accessible=True,
            retention_days=30,
        )["status"]
        == "PASS"
    )
    assert (
        module.evaluate_backup_policy(
            versioning_enabled=False,
            encryption_envelopes=3,
            object_count=4,
            key_accessible=False,
            retention_days=0,
        )["status"]
        == "FAIL"
    )
    assert (
        module.evaluate_backup_policy(
            versioning_enabled=True,
            encryption_envelopes=0,
            object_count=0,
            key_accessible=True,
            retention_days=30,
        )["status"]
        == "FAIL"
    )


def test_restore_receipt_measures_rpo_rto_and_all_smokes() -> None:
    module = _module()
    backup = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
    started = datetime(2026, 9, 5, 20, 10, tzinfo=timezone.utc)
    finished = datetime(2026, 9, 5, 20, 12, 30, tzinfo=timezone.utc)
    receipt = module.restore_receipt(
        source_revision="a" * 40,
        backup_completed_at=backup,
        restore_started_at=started,
        restore_finished_at=finished,
        reconciliation={
            "db_reference_count": 1,
            "object_count": 1,
            "missing_object_count": 0,
            "orphan_object_count": 0,
            "missing_object_key_hashes": [],
            "orphan_object_key_hashes": [],
        },
        smoke={"migration": True, "auth": True, "retrieval": True, "citation": True},
        backup_manifest_sha256="b" * 64,
        executor_repository_revision="f" * 40,
        source_database_identity={
            "container_id": "c" * 64,
            "database": "source",
            "system_identifier": "1",
        },
        target_database_identity={
            "container_id": "d" * 64,
            "database": "restore",
            "system_identifier": "2",
        },
        source_object_identity=(frozenset({"192.0.2.1"}), 9000),
        target_object_identity=(frozenset({"192.0.2.2"}), 9000),
    )
    assert receipt["status"] == "PASS"
    assert receipt["rpo_seconds"] == 600
    assert receipt["rto_seconds"] == 150
    assert receipt["release_gate_eligible"] is False
    assert set(receipt["smoke"]) == {"migration", "auth", "retrieval", "citation"}
    assert json.loads(module._encode_public_json(receipt))["status"] == "PASS"


def test_restore_receipt_fails_when_any_gate_fails() -> None:
    module = _module()
    now = datetime.now(timezone.utc)
    receipt = module.restore_receipt(
        source_revision="a" * 40,
        backup_completed_at=now,
        restore_started_at=now,
        restore_finished_at=now,
        reconciliation={
            "db_reference_count": 1,
            "object_count": 0,
            "missing_object_count": 1,
            "orphan_object_count": 0,
            "missing_object_key_hashes": ["e" * 64],
            "orphan_object_key_hashes": [],
        },
        smoke={"migration": True, "auth": True, "retrieval": True, "citation": False},
        backup_manifest_sha256="b" * 64,
        executor_repository_revision="f" * 40,
        source_database_identity={
            "container_id": "c" * 64,
            "database": "source",
            "system_identifier": "1",
        },
        target_database_identity={
            "container_id": "d" * 64,
            "database": "restore",
            "system_identifier": "2",
        },
        source_object_identity=(frozenset({"192.0.2.1"}), 9000),
        target_object_identity=(frozenset({"192.0.2.2"}), 9000),
    )
    assert receipt["status"] == "FAIL"


def test_corrupt_backup_is_rejected_before_target_probe(tmp_path, monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module, "_repository_revision", lambda *_args, **_kwargs: "f" * 40
    )
    manifest, _ = _private_manifest(tmp_path)
    manifest["postgres_dump_sha256"] = "b" * 64
    encoded = json.dumps(manifest).encode()
    (tmp_path / "backup-manifest.private.json").write_bytes(encoded)
    expected_sha = hashlib.sha256(encoded).hexdigest()
    target_probed = False

    def forbidden_probe(*_args, **_kwargs):
        nonlocal target_probed
        target_probed = True
        raise AssertionError("target inspected before backup validation")

    monkeypatch.setattr(module, "_container_id", forbidden_probe)
    with pytest.raises(module.DrError, match="binding mismatch"):
        module.run_restore_drill(
            repo=REPO,
            backup_dir=tmp_path,
            target_database_url="postgresql://u@target:5432/restore",
            target_postgres_container=None,
            source_minio_endpoint="http://source:9000",
            target_minio_endpoint="http://target:9000",
            source_bucket="source",
            target_bucket="restore",
            target_minio_access_key="unused",
            target_minio_secret_key="unused",
            expected_head="cv3_00000006",
            expected_manifest_sha256=expected_sha,
            encryption_key=TEST_ENCRYPTION_KEY_B64,
        )
    assert target_probed is False


def test_manifest_sha_schema_encryption_and_file_type_are_strict(tmp_path) -> None:
    module = _module()
    manifest, expected_sha = _private_manifest(tmp_path)
    loaded, actual_sha = module._stable_json_manifest(
        tmp_path / "backup-manifest.private.json", expected_sha
    )
    assert actual_sha == expected_sha
    dump, objects = module._validate_backup_materials(
        tmp_path, loaded, encryption_key=TEST_ENCRYPTION_KEY
    )
    dump.close()
    for _, stream in objects:
        stream.close()

    with pytest.raises(module.DrError, match="binding mismatch"):
        module._stable_json_manifest(
            tmp_path / "backup-manifest.private.json", "f" * 64
        )
    manifest["object_inventory"][0]["encrypted_envelope"] = False  # type: ignore[index]
    manifest["object_inventory_digest"] = hashlib.sha256(
        json.dumps(
            manifest["object_inventory"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    with pytest.raises(module.DrError, match="inventory entry"):
        module._validate_backup_materials(
            tmp_path, manifest, encryption_key=TEST_ENCRYPTION_KEY
        )

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (module.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(module.DrError, match="size or file-type"):
        module._stable_json_manifest(
            oversized, hashlib.sha256(oversized.read_bytes()).hexdigest()
        )
    symlink = tmp_path / "manifest-link.json"
    symlink.symlink_to(tmp_path / "backup-manifest.private.json")
    with pytest.raises(module.DrError, match="unavailable"):
        module._stable_json_manifest(symlink, expected_sha)


def test_target_container_requires_label_and_database_must_be_absent(
    monkeypatch,
) -> None:
    module = _module()
    full_id = "a" * 64
    answers = iter([full_id, "true", "false"])
    monkeypatch.setattr(module, "_run_text", lambda *_args, **_kwargs: next(answers))
    with pytest.raises(module.DrError, match="required target label"):
        module._container_id("candidate", require_restore_label=True)

    answers = iter(["t", "f"])
    monkeypatch.setattr(module, "_run_text", lambda *_args, **_kwargs: next(answers))
    assert (
        module._target_database_absent(full_id, user="postgres", database="restore")
        is False
    )
    assert (
        module._target_database_absent(full_id, user="postgres", database="restore")
        is True
    )


def test_every_envelope_is_authenticated_and_object_key_limit_is_exact(
    tmp_path,
) -> None:
    module = _module()
    wrong_dir = tmp_path / "wrong-key"
    manifest, _ = _private_manifest(wrong_dir)
    with pytest.raises(module.DrError, match="authentication failed"):
        module._validate_backup_materials(wrong_dir, manifest, encryption_key=b"z" * 32)

    boundary_dir = tmp_path / "boundary"
    boundary_manifest, _ = _private_manifest(
        boundary_dir, object_key="k" * module.MAX_KEY_BYTES
    )
    dump, objects = module._validate_backup_materials(
        boundary_dir, boundary_manifest, encryption_key=TEST_ENCRYPTION_KEY
    )
    dump.close()
    for _, stream in objects:
        stream.close()

    too_long_dir = tmp_path / "too-long"
    too_long_manifest, _ = _private_manifest(
        too_long_dir, object_key="k" * (module.MAX_KEY_BYTES + 1)
    )
    with pytest.raises(module.DrError, match="inventory entry"):
        module._validate_backup_materials(
            too_long_dir, too_long_manifest, encryption_key=TEST_ENCRYPTION_KEY
        )


def test_backup_with_wrong_key_never_emits_completed_manifest(
    tmp_path, monkeypatch
) -> None:
    module = _module()
    monkeypatch.setattr(
        module, "_repository_revision", lambda *_args, **_kwargs: "f" * 40
    )
    object_key = "synthetic/object"
    nonce = bytes(range(12))
    payload = (
        b"CVENC1\x00"
        + nonce
        + AESGCM(TEST_ENCRYPTION_KEY).encrypt(nonce, b"payload", object_key.encode())
    )

    class Response:
        def __init__(self):
            self._stream = io.BytesIO(payload)

        def read(self, size):
            return self._stream.read(size)

        def close(self):
            pass

        def release_conn(self):
            pass

    class Item:
        object_name = object_key

    class Versioning:
        status = "Enabled"

    class Client:
        def bucket_exists(self, _bucket):
            return True

        def get_bucket_versioning(self, _bucket):
            return Versioning()

        def list_objects(self, _bucket, recursive):
            assert recursive is True
            return [Item()]

        def get_object(self, _bucket, _key):
            return Response()

    monkeypatch.setattr(module, "_container_id", lambda *_args, **_kwargs: "a" * 64)
    monkeypatch.setattr(
        module,
        "_postgres_container_identity",
        lambda *_args, **_kwargs: {
            "container_id": "a" * 64,
            "database": "context",
            "system_identifier": "12345",
        },
    )
    monkeypatch.setattr(
        module,
        "_resolved_endpoint",
        lambda *_args: (frozenset({"192.0.2.10"}), 9000),
    )
    monkeypatch.setattr(module, "_minio_client", lambda *_args: Client())
    monkeypatch.setattr(
        module,
        "_dump_database",
        lambda **kwargs: kwargs["destination"].write_bytes(b"pgdump"),
    )
    output_dir = tmp_path / "backup"
    wrong_key = base64.b64encode(b"z" * 32).decode()
    with pytest.raises(module.DrError, match="authentication failed"):
        module.create_backup(
            repo=REPO,
            output_dir=output_dir,
            database_url="postgresql://postgres@source:5432/context",
            postgres_container="source",
            minio_endpoint="http://source:9000",
            minio_access_key="unused",
            minio_secret_key="unused",
            minio_bucket="source",
            encryption_key=wrong_key,
            retention_days=30,
        )
    assert not (output_dir / "backup-manifest.private.json").exists()


def test_output_is_reserved_and_unexpected_errors_are_sanitized(
    tmp_path, monkeypatch
) -> None:
    module = _module()
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(
        module,
        "dry_run_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret-value")),
    )
    result = module.main(["--dry-run", "--json-output", str(output)])
    assert result == 3
    assert output.stat().st_mode & 0o777 == 0o600
    rendered = output.read_text()
    assert "secret-value" not in rendered
    assert json.loads(rendered)["error_code"] == "INTERNAL_ERROR"
    assert module.main(["--dry-run", "--json-output", str(output)]) == 2


def test_public_receipt_schema_rejects_drift() -> None:
    module = _module()
    receipt = module.dry_run_plan(REPO, 30)
    receipt["debug_value"] = "must-not-escape"
    with pytest.raises(module.DrError, match="not allowlisted"):
        module._encode_public_json(receipt)


def test_docker_subprocess_environment_excludes_application_secrets(
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setenv("OBJECT_STORAGE_ENCRYPTION_KEY", "must-not-propagate")
    monkeypatch.setenv("DATABASE_URL", "must-not-propagate")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    projected = module._docker_env()
    assert projected["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert "OBJECT_STORAGE_ENCRYPTION_KEY" not in projected
    assert "DATABASE_URL" not in projected


def test_effectful_repository_provenance_rejects_dirty_tree(tmp_path) -> None:
    module = _module()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "a11@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "A11 Test"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    revision = module._repository_revision(tmp_path, require_clean=True)
    assert len(revision) == 40
    (tmp_path / "untracked.txt").write_text("dirty\n")
    with pytest.raises(module.DrError, match="clean repository"):
        module._repository_revision(tmp_path, require_clean=True)


def test_same_postgres_cluster_rejects_before_target_effect(
    monkeypatch, tmp_path
) -> None:
    module = _module()
    monkeypatch.setattr(
        module, "_repository_revision", lambda *_args, **_kwargs: "f" * 40
    )
    manifest = {
        "source_database_identity": {
            "container_id": "a" * 64,
            "database": "context",
            "system_identifier": "12345",
        }
    }
    monkeypatch.setattr(
        module, "_stable_json_manifest", lambda *_args: (manifest, "f" * 64)
    )
    monkeypatch.setattr(
        module,
        "_validate_backup_materials",
        lambda *_args, **_kwargs: (io.BytesIO(b"dump"), []),
    )
    monkeypatch.setattr(module, "_container_id", lambda *_args, **_kwargs: "b" * 64)
    monkeypatch.setattr(
        module,
        "_postgres_container_identity",
        lambda *_args, **_kwargs: {
            "container_id": "b" * 64,
            "database": "postgres",
            "system_identifier": "12345",
        },
    )
    monkeypatch.setattr(
        module,
        "_create_target_database",
        lambda *_args, **_kwargs: pytest.fail("effect occurred"),
    )
    with pytest.raises(module.DrError, match="clusters must differ"):
        module.run_restore_drill(
            repo=REPO,
            backup_dir=tmp_path,
            target_database_url="postgresql://postgres@target:5432/restore",
            target_postgres_container="restore",
            source_minio_endpoint="http://source:9000",
            target_minio_endpoint="http://target:9000",
            source_bucket="source",
            target_bucket="restore",
            target_minio_access_key="unused",
            target_minio_secret_key="unused",
            expected_head="cv3_00000006",
            expected_manifest_sha256="f" * 64,
            encryption_key=TEST_ENCRYPTION_KEY_B64,
        )


def test_existing_target_bucket_rejects_before_database_create(
    monkeypatch, tmp_path
) -> None:
    module = _module()
    monkeypatch.setattr(
        module, "_repository_revision", lambda *_args, **_kwargs: "f" * 40
    )
    manifest = {
        "source_database_identity": {
            "container_id": "a" * 64,
            "database": "context",
            "system_identifier": "12345",
        },
        "source_object_identity": {
            "resolved_addresses": ["192.0.2.10"],
            "port": 9000,
            "bucket": "source",
        },
    }
    monkeypatch.setattr(
        module, "_stable_json_manifest", lambda *_args: (manifest, "f" * 64)
    )
    monkeypatch.setattr(
        module,
        "_validate_backup_materials",
        lambda *_args, **_kwargs: (io.BytesIO(b"dump"), []),
    )
    monkeypatch.setattr(module, "_container_id", lambda *_args, **_kwargs: "b" * 64)
    monkeypatch.setattr(
        module,
        "_postgres_container_identity",
        lambda *_args, **_kwargs: {
            "container_id": "b" * 64,
            "database": "postgres",
            "system_identifier": "67890",
        },
    )
    monkeypatch.setattr(module, "_network_cluster_identity", lambda *_args: "67890")
    monkeypatch.setattr(
        module, "_target_database_absent", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        module,
        "_resolved_endpoint",
        lambda endpoint: (
            (frozenset({"192.0.2.10"}), 9000)
            if "source" in endpoint
            else (frozenset({"192.0.2.20"}), 9000)
        ),
    )

    class ExistingBucket:
        def bucket_exists(self, _bucket):
            return True

    monkeypatch.setattr(module, "_minio_client", lambda *_args: ExistingBucket())
    monkeypatch.setattr(
        module,
        "_create_target_database",
        lambda *_args, **_kwargs: pytest.fail("database create effect occurred"),
    )
    with pytest.raises(module.DrError, match="must not already exist"):
        module.run_restore_drill(
            repo=REPO,
            backup_dir=tmp_path,
            target_database_url="postgresql://postgres@target:5432/restore",
            target_postgres_container="restore",
            source_minio_endpoint="http://source:9000",
            target_minio_endpoint="http://target:9000",
            source_bucket="source",
            target_bucket="restore",
            target_minio_access_key="unused",
            target_minio_secret_key="unused",
            expected_head="cv3_00000006",
            expected_manifest_sha256="f" * 64,
            encryption_key=TEST_ENCRYPTION_KEY_B64,
        )


def test_required_a11_runbooks_have_operator_contract_sections() -> None:
    runbook_dir = REPO / "document-rag-platform/docs/runbooks"
    names = {
        "a11-backup-restore.md",
        "installation-first-boot.md",
        "migration-lineage-recovery.md",
        "reindex-profile-change.md",
        "provider-ca-key-rotation.md",
        "stuck-job-outbox-lease.md",
        "object-gc-quarantine.md",
        "repository-ingestion-incident.md",
        "degraded-readiness.md",
        "security-incident.md",
        "release-rollback.md",
    }
    required = {
        "Prerequisites",
        "Dry-run",
        "Exact commands",
        "Expected output",
        "Stop conditions",
        "Rollback",
        "Evidence / receipt",
    }
    for name in names:
        text = (runbook_dir / name).read_text(encoding="utf-8")
        assert "Last verified SHA" in text
        assert "2026-09-06" in text
        for heading in required:
            assert f"## {heading}" in text, f"{name}: missing {heading}"
