"""Fail-closed contracts for A11 supply-chain, SLO and RC evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[4] / "scripts/a11_release_gate.py"
SPEC = importlib.util.spec_from_file_location("a11_release_gate", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
REAL_REPO = SCRIPT.parents[1]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    dockerfile = repo / "document-rag-platform/services/backend/Dockerfile"
    lockfile = repo / "document-rag-platform/services/backend/uv.lock"
    runbook = repo / "document-rag-platform/docs/runbooks/action.md"
    dockerfile.parent.mkdir(parents=True)
    runbook.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    lockfile.write_text("version = 1\n", encoding="utf-8")
    runbook.write_text("# Action\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        "evidence/\ndocument-rag-platform/infra/release/known-risks.json\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "A11 Test")
    _git(repo, "config", "user.email", "a11@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _slo_inputs(repo: Path, head: str) -> tuple[Path, Path]:
    policy = repo / "evidence/policy.json"
    measurements = repo / "evidence/measurements.json"
    _write(
        policy,
        {
            "schema_version": "1.0",
            "status": "ACTIVE_BASELINE_BOUND",
            "baseline_receipt_sha256": "b" * 64,
            "maximum_measurement_age_seconds": 900,
            "on_call": {
                "primary": "primary-on-call",
                "escalation_target": "service-owner",
            },
            "slos": [
                {
                    "name": "permission_leakage_count",
                    "comparator": "exactly",
                    "threshold": 0,
                    "owner": "security-owner",
                    "escalation": "stop access and investigate",
                    "runbook": "document-rag-platform/docs/runbooks/action.md",
                },
                {
                    "name": "api_availability_percent",
                    "comparator": "at_least",
                    "threshold": 99.9,
                    "owner": "service-owner",
                    "escalation": "freeze features",
                    "runbook": "document-rag-platform/docs/runbooks/action.md",
                },
            ],
        },
    )
    _write(
        measurements,
        {
            "source_sha": head,
            "observed_at_utc": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "window_seconds": 300,
            "measurements": {
                "permission_leakage_count": 0,
                "api_availability_percent": 99.95,
            },
        },
    )
    return policy, measurements


def test_slo_pass_binds_exact_source_and_actionable_routes(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    policy, measurements = _slo_inputs(repo, head)

    receipt = gate.evaluate_slo(
        repo=repo, policy_path=policy, measurements_path=measurements
    )

    assert receipt["status"] == "PASS"
    assert receipt["source_sha"] == head
    assert receipt["feature_freeze_required"] is False
    assert receipt["on_call"]["primary"] == "primary-on-call"
    assert all(item["owner"] and item["runbook"] for item in receipt["results"])


def test_slo_breach_fails_and_prioritizes_reliability(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    policy, measurements = _slo_inputs(repo, head)
    payload = json.loads(measurements.read_text())
    payload["measurements"]["permission_leakage_count"] = 1
    _write(measurements, payload)

    receipt = gate.evaluate_slo(
        repo=repo, policy_path=policy, measurements_path=measurements
    )

    assert receipt["status"] == "FAIL"
    assert receipt["breached_slos"] == ["permission_leakage_count"]
    assert receipt["feature_freeze_required"] is True
    assert receipt["reliability_work_priority"] == "IMMEDIATE"


def test_slo_rejects_non_finite_values(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    policy, measurements = _slo_inputs(repo, head)
    payload = json.loads(measurements.read_text())
    payload["measurements"]["api_availability_percent"] = float("inf")
    _write(measurements, payload)

    with pytest.raises(gate.GateError, match="must be finite"):
        gate.evaluate_slo(repo=repo, policy_path=policy, measurements_path=measurements)


def test_slo_provisional_policy_cannot_emit_operational_receipt(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    policy, measurements = _slo_inputs(repo, head)
    payload = json.loads(policy.read_text())
    payload["status"] = "PROVISIONAL_BLOCKED_PENDING_BASELINE"
    payload["baseline_receipt_sha256"] = None
    _write(policy, payload)

    with pytest.raises(gate.GateError, match="not active"):
        gate.evaluate_slo(repo=repo, policy_path=policy, measurements_path=measurements)


@pytest.mark.parametrize("mutation", ["unknown", "wrong_sha", "stale"])
def test_slo_unknown_metric_or_stale_sha_is_rejected(
    tmp_path: Path, mutation: str
) -> None:
    repo, head = _repo(tmp_path)
    policy, measurements = _slo_inputs(repo, head)
    payload = json.loads(measurements.read_text())
    if mutation == "unknown":
        payload["measurements"]["raw_project_id"] = 1
    elif mutation == "wrong_sha":
        payload["source_sha"] = "a" * 40
    else:
        payload["observed_at_utc"] = "2020-01-01T00:00:00Z"
    _write(measurements, payload)

    with pytest.raises(gate.GateError):
        gate.evaluate_slo(repo=repo, policy_path=policy, measurements_path=measurements)


def test_image_subject_requires_oci_revision_equal_to_head(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    output = repo / "evidence/subject.json"
    digest = "sha256:" + "b" * 64
    sbom = repo / "evidence/sbom.json"
    scan = repo / "evidence/scan.json"
    _write(
        sbom,
        {
            "bomFormat": "CycloneDX",
            "metadata": {
                "component": {
                    "type": "container",
                    "name": "candidate:test",
                    "bom-ref": f"pkg:oci/candidate@{digest}",
                }
            },
            "components": [{"name": "openssl"}],
        },
    )
    _write(
        scan,
        {
            "ArtifactName": "candidate:test",
            "Metadata": {"ImageID": digest},
            "Results": [],
        },
    )

    def run(command, **kwargs):
        assert command[:3] == ["docker", "image", "inspect"]
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                [
                    {
                        "Id": digest,
                        "Config": {
                            "Labels": {"org.opencontainers.image.revision": head}
                        },
                    }
                ]
            ),
            "",
        )

    subject = gate.create_image_subject(
        repo=repo,
        image_ref="candidate:test",
        sbom_path=sbom,
        scan_path=scan,
        output=output,
        run=run,
    )

    assert subject["source_sha"] == head
    assert subject["image_digest"] == "sha256:" + "b" * 64
    assert oct(output.stat().st_mode & 0o777) == "0o600"


def _image_evidence(repo: Path, head: str) -> tuple[Path, Path, Path, Path]:
    image_digest = "sha256:" + "b" * 64
    subject = repo / "evidence/subject.json"
    sbom = repo / "evidence/sbom.json"
    scan = repo / "evidence/scan.json"
    bundle = repo / "evidence/signature.json"
    _write(subject, {"source_sha": head, "image_digest": image_digest})
    _write(
        sbom,
        {
            "bomFormat": "CycloneDX",
            "metadata": {
                "component": {
                    "type": "container",
                    "name": "candidate:test",
                    "bom-ref": f"pkg:oci/candidate@{image_digest}",
                }
            },
            "components": [{"name": "openssl"}],
        },
    )
    _write(
        scan,
        {
            "ArtifactName": "candidate:test",
            "Metadata": {"ImageID": image_digest},
            "Results": [],
        },
    )
    subject_payload = json.loads(subject.read_text())
    subject_payload.update(
        {
            "image_ref": "candidate:test",
            "image_sbom_sha256": gate.sha256(sbom),
            "image_scan_sha256": gate.sha256(scan),
        }
    )
    _write(subject, subject_payload)
    _write(bundle, {"mediaType": "application/vnd.dev.sigstore.bundle+json"})
    return subject, sbom, scan, bundle


def test_image_verifier_binds_digest_scan_sbom_and_cosign_identity(
    tmp_path: Path,
) -> None:
    repo, head = _repo(tmp_path)
    subject, sbom, scan, bundle = _image_evidence(repo, head)
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "Verified OK", "")

    receipt = gate.verify_image_evidence(
        repo=repo,
        subject_path=subject,
        sbom_path=sbom,
        scan_path=scan,
        signature_bundle=bundle,
        certificate_identity=(
            "https://github.com/mehmet-karacan/context-vault/.github/workflows/"
            "ci-security.yml@refs/heads/main"
        ),
        certificate_oidc_issuer="https://token.actions.githubusercontent.com",
        run=run,
    )

    assert receipt["status"] == "PASS"
    assert receipt["critical_high_findings"] == []
    assert "--certificate-identity" in commands[0]
    assert str(subject) == commands[0][-1]

    sbom_payload = json.loads(sbom.read_text())
    sbom_payload["components"].append({"name": "tampered-after-signing"})
    _write(sbom, sbom_payload)
    with pytest.raises(gate.GateError, match="does not bind the image SBOM hash"):
        gate.verify_image_evidence(
            repo=repo,
            subject_path=subject,
            sbom_path=sbom,
            scan_path=scan,
            signature_bundle=bundle,
            certificate_identity=(
                "https://github.com/mehmet-karacan/context-vault/.github/workflows/"
                "ci-security.yml@refs/heads/main"
            ),
            certificate_oidc_issuer=gate.EXPECTED_OIDC_ISSUER,
            run=run,
        )


def test_image_verifier_fails_on_high_finding_or_digest_drift(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    subject, sbom, scan, bundle = _image_evidence(repo, head)
    payload = json.loads(scan.read_text())
    payload["Results"] = [
        {"Vulnerabilities": [{"VulnerabilityID": "CVE-TEST-1", "Severity": "HIGH"}]}
    ]
    _write(scan, payload)
    signed_subject = json.loads(subject.read_text())
    signed_subject["image_scan_sha256"] = gate.sha256(scan)
    _write(subject, signed_subject)
    receipt = gate.verify_image_evidence(
        repo=repo,
        subject_path=subject,
        sbom_path=sbom,
        scan_path=scan,
        signature_bundle=bundle,
        certificate_identity=(
            "https://github.com/mehmet-karacan/context-vault/.github/workflows/"
            "ci-security.yml@refs/heads/main"
        ),
        certificate_oidc_issuer=gate.EXPECTED_OIDC_ISSUER,
        run=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    assert receipt["status"] == "FAIL"
    assert receipt["critical_high_findings"] == [
        {"id": "CVE-TEST-1", "severity": "HIGH"}
    ]

    payload["Metadata"]["ImageID"] = "sha256:" + "c" * 64
    _write(scan, payload)
    with pytest.raises(gate.GateError, match="not bound"):
        gate.verify_image_evidence(
            repo=repo,
            subject_path=subject,
            sbom_path=sbom,
            scan_path=scan,
            signature_bundle=bundle,
            certificate_identity=(
                "https://github.com/mehmet-karacan/context-vault/.github/workflows/"
                "ci-security.yml@refs/heads/main"
            ),
            certificate_oidc_issuer=gate.EXPECTED_OIDC_ISSUER,
            run=lambda command, **kwargs: subprocess.CompletedProcess(
                command, 0, "", ""
            ),
        )


def _rc_inputs(repo: Path, head: str, *, risk_status: str) -> tuple[Path, Path]:
    evidence = repo / "evidence"
    gates = []
    for name in sorted(gate.REQUIRED_RC_GATES):
        receipt = evidence / f"{name}.json"
        payload = {
            "schema_version": "1.0",
            "receipt_type": gate.REQUIRED_RC_RECEIPT_TYPES[name],
            "status": "PASS",
            "source_sha": head,
        }
        if name == "image_security":
            payload["image_digest"] = "sha256:" + "d" * 64
        _write(receipt, payload)
        gates.append(
            {
                "name": name,
                "receipt": str(receipt.relative_to(repo)),
                "sha256": gate.sha256(receipt),
            }
        )
    manifest = evidence / "manifest.json"
    risks = repo / "document-rag-platform/infra/release/known-risks.json"
    _write(manifest, {"schema_version": "1.0", "source_sha": head, "gates": gates})
    _write(
        risks,
        {
            "schema_version": "1.0",
            "risks": [
                {
                    "id": "R-1",
                    "severity": "P1",
                    "status": risk_status,
                    "owner": "owner",
                }
            ],
        },
    )
    return manifest, risks


def test_rc_passes_only_exact_gate_set_hashes_sha_digest_and_closed_p1(
    tmp_path: Path,
) -> None:
    repo, head = _repo(tmp_path)
    manifest, risks = _rc_inputs(repo, head, risk_status="CLOSED")

    receipt = gate.evaluate_release_candidate(
        repo=repo, manifest_path=manifest, risks_path=risks
    )

    assert receipt["status"] == "PASS"
    assert receipt["failures"] == []
    assert receipt["source_sha"] == head
    assert len(receipt["verified_gate_names"]) == 11


def test_rc_open_p1_and_dirty_worktree_fail_closed(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    manifest, risks = _rc_inputs(repo, head, risk_status="OPEN")
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")

    receipt = gate.evaluate_release_candidate(
        repo=repo, manifest_path=manifest, risks_path=risks
    )

    assert receipt["status"] == "FAIL"
    assert receipt["blocking_known_risks"] == ["R-1"]
    assert receipt["failures"] == ["blocking_known_risks", "clean_worktree"]


def test_rc_rejects_wrong_receipt_type_and_symlink_escape(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    manifest, risks = _rc_inputs(repo, head, risk_status="CLOSED")
    payload = json.loads(manifest.read_text())
    entry = next(item for item in payload["gates"] if item["name"] == "locked_build")
    receipt = repo / entry["receipt"]
    receipt_payload = json.loads(receipt.read_text())
    receipt_payload["receipt_type"] = "unrelated-pass"
    _write(receipt, receipt_payload)
    entry["sha256"] = gate.sha256(receipt)
    _write(manifest, payload)
    with pytest.raises(gate.GateError, match="receipt type mismatch"):
        gate.evaluate_release_candidate(
            repo=repo, manifest_path=manifest, risks_path=risks
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = outside / "receipt.json"
    _write(
        escaped,
        {
            "schema_version": "1.0",
            "receipt_type": gate.REQUIRED_RC_RECEIPT_TYPES["locked_build"],
            "status": "PASS",
            "source_sha": head,
        },
    )
    link = repo / "escaped"
    link.symlink_to(outside, target_is_directory=True)
    payload = json.loads(manifest.read_text())
    entry = next(item for item in payload["gates"] if item["name"] == "locked_build")
    entry["receipt"] = "escaped/receipt.json"
    entry["sha256"] = gate.sha256(escaped)
    _write(manifest, payload)
    with pytest.raises(gate.GateError, match="inside the repository"):
        gate.evaluate_release_candidate(
            repo=repo, manifest_path=manifest, risks_path=risks
        )


def test_receipt_write_is_exclusive_private_and_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "receipt.json"
    gate._write_private(target, {"status": "PASS"})
    assert stat_mode(target) == 0o600
    with pytest.raises(gate.GateError):
        gate._write_private(target, {"status": "PASS"})

    real = tmp_path / "real.json"
    _write(real, {})
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(gate.GateError, match="non-symlink"):
        gate._stable_json(link)


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_repository_contract_pins_image_identity_tools_and_complete_slo_set() -> None:
    dockerfile = (
        REAL_REPO / "document-rag-platform/services/backend/Dockerfile"
    ).read_text(encoding="utf-8")
    workflow = (REAL_REPO / ".github/workflows/ci-security.yml").read_text(
        encoding="utf-8"
    )
    policy = json.loads(
        (REAL_REPO / "document-rag-platform/infra/release/slo-policy.json").read_text(
            encoding="utf-8"
        )
    )

    assert "ARG SOURCE_REVISION" in dockerfile
    assert 'org.opencontainers.image.revision="${SOURCE_REVISION}"' in dockerfile
    assert (
        "aquasecurity/trivy-action@a9c7b0f06e461e9d4b4d1711f154ee024b8d7ab8" in workflow
    )
    assert workflow.count("version: v0.70.0") == 2
    assert (
        "sigstore/cosign-installer@d58896d6a1865668819e1d91763c7751a165e159" in workflow
    )
    assert "cosign-release: v2.5.3" in workflow
    assert "id-token: write" in workflow
    assert policy["status"] == "PROVISIONAL_BLOCKED_PENDING_BASELINE"
    assert policy["baseline_receipt_sha256"] is None
    assert {item["name"] for item in policy["slos"]} == {
        "api_availability_percent",
        "api_p95_latency_ms",
        "ingestion_completion_p95_seconds",
        "ingestion_failure_percent",
        "outbox_oldest_age_seconds",
        "permission_leakage_count",
        "invalid_citation_count",
        "backup_age_seconds",
        "restore_drill_age_seconds",
        "restore_drill_success_percent",
        "real_benchmark_regression_percent",
    }
