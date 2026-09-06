"""Fail-closed contract tests for the local-only provisional SLO rehearsal."""

from __future__ import annotations

import importlib.util
import json
import hashlib
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts/slo_rehearsal.py"
POLICY = REPO / "document-rag-platform/infra/a11-slo-provisional.json"
CANONICAL_POLICY = REPO / "document-rag-platform/infra/release/slo-policy.json"


def _module():
    spec = importlib.util.spec_from_file_location("slo_rehearsal", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _samples(*, breach: bool = True) -> dict:
    return {
        "schema": "context-vault-slo-synthetic-samples/v1",
        "classification": "synthetic",
        "samples": [
            {
                "api_available": True,
                "api_latency_ms": 2500 if breach else 100,
                "ingestion_completed": True,
                "ingestion_duration_seconds": 40,
                "outbox_oldest_age_seconds": 10,
                "backup_age_seconds": 3600,
                "restore_drill_succeeded": True,
                "restore_drill_age_seconds": 86400,
                "benchmark_regression": False,
                "permission_leakage_count": 0,
                "invalid_citation_count": 0,
            }
        ],
    }


def test_rehearsal_receipt_is_explicitly_synthetic_local_and_content_free() -> None:
    module = _module()
    policy, policy_sha = module.stable_json(POLICY)
    canonical, canonical_sha = module.stable_json(CANONICAL_POLICY)
    receipt = module.build_receipt(
        policy,
        canonical,
        _samples(),
        policy_sha256=policy_sha,
        canonical_policy_sha256=canonical_sha,
        samples_sha256="a" * 64,
        generated_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        expect_alert=True,
    )
    assert receipt["status"] == "PASS"
    assert receipt["classification"] == "synthetic"
    assert receipt["production_baseline"] is False
    assert receipt["production_owner_approval"] is False
    assert receipt["delivery"] == {
        "mode": "local_receipt_only",
        "attempted": True,
        "delivered": True,
        "remote_delivery": False,
    }
    assert receipt["evaluation"] == {"state": "ALERT", "alert_count": 1}
    encoded = json.dumps(receipt)
    assert "private" not in encoded
    assert "/Users/" not in encoded


def test_provisional_policy_is_hash_and_threshold_bound_to_canonical() -> None:
    module = _module()
    policy, _ = module.stable_json(POLICY)
    canonical, canonical_sha = module.stable_json(CANONICAL_POLICY)
    thresholds = module.validate_policy(
        policy,
        canonical,
        canonical_sha256=canonical_sha,
    )
    assert thresholds["api_availability_ratio"] == 0.999
    assert thresholds["backup_age_seconds"] == 21600

    drifted = json.loads(json.dumps(policy))
    drifted["thresholds"]["backup_age_seconds"] += 1
    with pytest.raises(module.SloRehearsalError, match="drifted"):
        module.validate_policy(
            drifted,
            canonical,
            canonical_sha256=canonical_sha,
        )


def test_operational_ages_require_eligible_non_mutating_receipts() -> None:
    module = _module()
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    backup = {
        "receipt_type": "backup-complete",
        "status": "PASS",
        "release_gate_eligible": True,
        "source_mutated": False,
        "credential_values_retained": False,
        "raw_object_names_retained_in_receipt": False,
        "completed_at_utc": "2026-09-06T10:00:00Z",
    }
    restore = {
        "receipt_type": "fresh-target-restore-drill",
        "status": "PASS",
        "release_gate_eligible": True,
        "fresh_target_verified": True,
        "source_mutated": False,
        "credential_values_retained": False,
        "raw_object_names_retained": False,
        "restore_finished_at_utc": "2026-09-06T11:30:00Z",
    }
    assert module.operational_ages(backup, restore, now=now) == {
        "backup.age_seconds": 7200,
        "restore_drill.age_seconds": 1800,
    }
    backup["source_mutated"] = True
    with pytest.raises(module.SloRehearsalError):
        module.operational_ages(backup, restore, now=now)


def test_cli_writes_exclusive_mode_0600_receipt(tmp_path: Path) -> None:
    module = _module()
    samples = tmp_path / "samples.json"
    samples.write_text(json.dumps(_samples()), encoding="utf-8")
    output = tmp_path / "receipt.json"
    assert (
        module.main(
            [
                "--policy",
                str(POLICY),
                "--canonical-policy",
                str(CANONICAL_POLICY),
                "--samples",
                str(samples),
                "--json-output",
                str(output),
                "--expect-alert",
            ]
        )
        == 0
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert (
        module.main(
            [
                "--policy",
                str(POLICY),
                "--canonical-policy",
                str(CANONICAL_POLICY),
                "--samples",
                str(samples),
                "--json-output",
                str(output),
            ]
        )
        == 2
    )


def test_cli_projects_only_bound_operational_ages(tmp_path: Path) -> None:
    module = _module()
    samples = tmp_path / "private-sample-name-must-not-leak.json"
    samples.write_text(json.dumps(_samples()), encoding="utf-8")
    now = datetime.now(timezone.utc)
    backup_payload = {
        "receipt_type": "backup-complete",
        "status": "PASS",
        "release_gate_eligible": True,
        "source_mutated": False,
        "credential_values_retained": False,
        "raw_object_names_retained_in_receipt": False,
        "completed_at_utc": (now - timedelta(hours=7)).isoformat(),
    }
    restore_payload = {
        "receipt_type": "fresh-target-restore-drill",
        "status": "PASS",
        "release_gate_eligible": True,
        "fresh_target_verified": True,
        "source_mutated": False,
        "credential_values_retained": False,
        "raw_object_names_retained": False,
        "restore_finished_at_utc": (now - timedelta(hours=1)).isoformat(),
    }
    backup = tmp_path / "private-backup-path.json"
    restore = tmp_path / "private-restore-path.json"
    backup_bytes = json.dumps(backup_payload).encode()
    restore_bytes = json.dumps(restore_payload).encode()
    backup.write_bytes(backup_bytes)
    restore.write_bytes(restore_bytes)
    output = tmp_path / "receipt.json"

    assert (
        module.main(
            [
                "--policy",
                str(POLICY),
                "--canonical-policy",
                str(CANONICAL_POLICY),
                "--samples",
                str(samples),
                "--backup-receipt",
                str(backup),
                "--restore-receipt",
                str(restore),
                "--json-output",
                str(output),
            ]
        )
        == 0
    )
    receipt = json.loads(output.read_text())
    assert set(receipt["operational_observation"]) == {
        "backup.age_seconds",
        "restore_drill.age_seconds",
    }
    assert receipt["operational_source_binding"] == {
        "backup_receipt_sha256": hashlib.sha256(backup_bytes).hexdigest(),
        "restore_receipt_sha256": hashlib.sha256(restore_bytes).hexdigest(),
    }
    assert receipt["baseline"]["backup_age_seconds"] >= 7 * 60 * 60
    assert any(alert["metric"] == "backup_age_seconds" for alert in receipt["alerts"])
    encoded = json.dumps(receipt)
    assert "private-" not in encoded
    assert str(tmp_path) not in encoded


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: {**value, "classification": "production"},
        lambda value: {**value, "raw_prompt": "private"},
        lambda value: {**value, "samples": []},
        lambda value: {
            **value,
            "samples": [{**value["samples"][0], "permission_leakage_count": 0.5}],
        },
    ],
)
def test_invalid_or_authoritative_claims_fail_before_output(
    tmp_path: Path, mutation
) -> None:
    module = _module()
    samples = tmp_path / "samples.json"
    samples.write_text(json.dumps(mutation(_samples())), encoding="utf-8")
    output = tmp_path / "receipt.json"
    assert (
        module.main(
            [
                "--policy",
                str(POLICY),
                "--canonical-policy",
                str(CANONICAL_POLICY),
                "--samples",
                str(samples),
                "--json-output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_symlink_and_oversized_sources_fail_closed(tmp_path: Path) -> None:
    module = _module()
    target = tmp_path / "target.json"
    target.write_text(json.dumps(_samples()), encoding="utf-8")
    symlink = tmp_path / "samples.json"
    symlink.symlink_to(target)
    with pytest.raises(module.SloRehearsalError):
        module.stable_json(symlink)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * module.MAX_INPUT_BYTES + b"}")
    with pytest.raises(module.SloRehearsalError):
        module.stable_json(oversized)
