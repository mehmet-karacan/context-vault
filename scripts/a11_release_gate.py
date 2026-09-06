#!/usr/bin/env python3
"""Fail-closed A11 image, SLO and release-candidate evidence gates.

The gate consumes public-safe receipts. It never reads credentials or application
content and it never performs a deployment. External scanners and cosign remain
the authorities for vulnerability and signature verification; this module binds
their outputs to one immutable source revision and image digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_JSON_BYTES = 4 * 1024 * 1024
REQUIRED_RC_GATES = frozenset(
    {
        "clean_clone",
        "empty_runtime",
        "locked_build",
        "blank_db_migration",
        "synthetic_e2e",
        "private_real_benchmark",
        "image_security",
        "backup_restore",
        "docs_status",
        "remote_ruleset_checks",
        "known_risks",
    }
)
REQUIRED_RC_RECEIPT_TYPES = {
    "clean_clone": "a13-clean-clone",
    "empty_runtime": "a13-empty-runtime",
    "locked_build": "a11-locked-build",
    "blank_db_migration": "migration-verification",
    "synthetic_e2e": "synthetic-e2e-verification",
    "private_real_benchmark": "a9-captured-real-benchmark",
    "image_security": "a11-image-security",
    "backup_restore": "fresh-target-restore-drill",
    "docs_status": "verified-status",
    "remote_ruleset_checks": "remote-ruleset-checks",
    "known_risks": "a11-known-risk-review",
}
ALLOWED_SLO_COMPARATORS = frozenset({"at_least", "at_most", "exactly"})
EXPECTED_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
EXPECTED_CERTIFICATE_IDENTITY = re.compile(
    r"^https://github\.com/mehmet-karacan/context-vault/\.github/workflows/"
    r"ci-security\.yml@refs/(?:heads/[A-Za-z0-9._/-]+|pull/[0-9]+/merge)$"
)
Run = Callable[..., subprocess.CompletedProcess[str]]


class GateError(RuntimeError):
    """Evidence is incomplete, malformed, stale or failing."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_json_with_sha(
    path: Path, *, maximum: int = MAX_JSON_BYTES
) -> tuple[dict[str, Any], str]:
    try:
        supplied = path.lstat()
    except OSError as error:
        raise GateError(f"missing evidence: {path}") from error
    if stat.S_ISLNK(supplied.st_mode) or not stat.S_ISREG(supplied.st_mode):
        raise GateError(f"evidence must be a regular non-symlink file: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise GateError(f"missing evidence: {path}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise GateError(f"evidence must be a regular non-symlink file: {path}")
        if before.st_size > maximum:
            raise GateError(f"evidence exceeds {maximum} bytes: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(maximum + 1)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if (
            len(raw) > maximum
            or identity_before != identity_after
            or len(raw) != before.st_size
        ):
            raise GateError(f"evidence changed while being read: {path}")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(f"evidence is not valid UTF-8 JSON: {path}") from error
    if not isinstance(payload, dict):
        raise GateError(f"evidence root must be an object: {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def _stable_json(path: Path, *, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    return _stable_json_with_sha(path, maximum=maximum)[0]


def _write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise GateError("receipt output must be a new file") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600 or not path.is_file() or path.is_symlink():
        raise GateError("receipt output is not a private regular file")


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise GateError("git inspection failed") from error


def _head(repo: Path) -> str:
    value = _git(repo, "rev-parse", "HEAD")
    if not GIT_SHA.fullmatch(value):
        raise GateError("HEAD is not a full commit SHA")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise GateError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GateError(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise GateError(f"{label} must be a UTC timestamp")
    return parsed


def _require_source_sha(value: Any, expected: str) -> None:
    if not isinstance(value, str) or not GIT_SHA.fullmatch(value) or value != expected:
        raise GateError("source revision does not match exact candidate HEAD")


def _repo_file(repo: Path, relative: str, *, label: str) -> Path:
    try:
        root = repo.resolve(strict=True)
        candidate = (root / relative).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError) as error:
        raise GateError(f"{label} must resolve inside the repository") from error
    return candidate


def _confined_path(repo: Path, path: Path, *, label: str) -> Path:
    try:
        root = repo.resolve(strict=True)
        candidate = path.resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError) as error:
        raise GateError(f"{label} must resolve inside the repository") from error
    return candidate


def _threshold_passes(comparator: str, value: float, threshold: float) -> bool:
    if comparator == "at_least":
        return value >= threshold
    if comparator == "at_most":
        return value <= threshold
    if comparator == "exactly":
        return value == threshold
    raise GateError(f"unsupported SLO comparator: {comparator}")


def evaluate_slo(
    *, repo: Path, policy_path: Path, measurements_path: Path
) -> dict[str, Any]:
    policy, policy_sha = _stable_json_with_sha(policy_path)
    measurements, measurements_sha = _stable_json_with_sha(measurements_path)
    head = _head(repo)
    if policy.get("schema_version") != "1.0":
        raise GateError("unsupported SLO policy schema")
    baseline_sha = policy.get("baseline_receipt_sha256")
    if (
        policy.get("status") != "ACTIVE_BASELINE_BOUND"
        or not isinstance(baseline_sha, str)
        or not SHA256.fullmatch(baseline_sha)
    ):
        raise GateError("SLO policy is not active and baseline-receipt-bound")
    on_call = policy.get("on_call")
    if (
        not isinstance(on_call, dict)
        or not isinstance(on_call.get("primary"), str)
        or not on_call["primary"].strip()
        or not isinstance(on_call.get("escalation_target"), str)
        or not on_call["escalation_target"].strip()
    ):
        raise GateError("SLO policy has no actionable on-call route")
    _require_source_sha(measurements.get("source_sha"), head)
    maximum_age = policy.get("maximum_measurement_age_seconds")
    window = measurements.get("window_seconds")
    if (
        not isinstance(maximum_age, int)
        or isinstance(maximum_age, bool)
        or not 1 <= maximum_age <= 86400
        or not isinstance(window, int)
        or isinstance(window, bool)
        or not 1 <= window <= 2_592_000
    ):
        raise GateError("SLO freshness/window contract is malformed")
    observed_at = _utc_timestamp(
        measurements.get("observed_at_utc"), label="measurement timestamp"
    )
    age = (datetime.now(timezone.utc) - observed_at).total_seconds()
    if age < -300 or age > maximum_age:
        raise GateError("SLO measurements are stale or from the future")
    observed = measurements.get("measurements")
    definitions = policy.get("slos")
    if not isinstance(observed, dict) or not isinstance(definitions, list):
        raise GateError("SLO inputs are malformed")
    names: list[str] = []
    results: list[dict[str, Any]] = []
    for definition in definitions:
        if not isinstance(definition, dict):
            raise GateError("SLO definition must be an object")
        name = definition.get("name")
        comparator = definition.get("comparator")
        threshold = definition.get("threshold")
        owner = definition.get("owner")
        escalation = definition.get("escalation")
        runbook = definition.get("runbook")
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or comparator not in ALLOWED_SLO_COMPARATORS
            or not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not math.isfinite(float(threshold))
            or not isinstance(owner, str)
            or not owner.strip()
            or not isinstance(escalation, str)
            or not escalation.strip()
            or not isinstance(runbook, str)
            or Path(runbook).is_absolute()
            or ".." in Path(runbook).parts
            or not _repo_file(repo, runbook, label="SLO runbook").is_file()
        ):
            raise GateError(f"invalid actionable SLO definition: {name!r}")
        value = observed.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise GateError(f"missing numeric SLO measurement: {name}")
        if not math.isfinite(float(value)):
            raise GateError(f"SLO measurement must be finite: {name}")
        passed = _threshold_passes(comparator, float(value), float(threshold))
        names.append(name)
        results.append(
            {
                "name": name,
                "value": value,
                "comparator": comparator,
                "threshold": threshold,
                "status": "PASS" if passed else "BREACH",
                "owner": owner,
                "escalation": escalation,
                "runbook": runbook,
            }
        )
    if set(observed) != set(names):
        raise GateError("SLO measurements must exactly match the policy allowlist")
    breached = sorted(item["name"] for item in results if item["status"] == "BREACH")
    return {
        "schema_version": "1.0",
        "receipt_type": "a11-slo-evaluation",
        "status": "PASS" if not breached else "FAIL",
        "source_sha": head,
        "evaluated_at_utc": _utc_now(),
        "policy_sha256": policy_sha,
        "baseline_receipt_sha256": baseline_sha,
        "measurements_sha256": measurements_sha,
        "results": results,
        "on_call": on_call,
        "breached_slos": breached,
        "feature_freeze_required": bool(breached),
        "reliability_work_priority": "NORMAL" if not breached else "IMMEDIATE",
        "contains_application_content": False,
        "contains_credentials": False,
    }


def _validate_sbom_binding(
    sbom: dict[str, Any], *, image_ref: str, image_digest: str
) -> None:
    metadata = sbom.get("metadata")
    component = metadata.get("component") if isinstance(metadata, dict) else None
    bom_ref = component.get("bom-ref") if isinstance(component, dict) else None
    if (
        sbom.get("bomFormat") != "CycloneDX"
        or not isinstance(sbom.get("components"), list)
        or not sbom["components"]
        or not isinstance(component, dict)
        or component.get("type") != "container"
        or component.get("name") != image_ref
        or not isinstance(bom_ref, str)
        or image_digest not in bom_ref
    ):
        raise GateError("image SBOM is not bound to the exact image ref and digest")


def _validate_scan_binding(
    scan: dict[str, Any], *, image_ref: str, image_digest: str
) -> None:
    metadata = scan.get("Metadata")
    if (
        scan.get("ArtifactName") != image_ref
        or not isinstance(metadata, dict)
        or metadata.get("ImageID") != image_digest
    ):
        raise GateError("Trivy report is not bound to the image subject ref and digest")


def create_image_subject(
    *,
    repo: Path,
    image_ref: str,
    sbom_path: Path,
    scan_path: Path,
    output: Path,
    run: Run = subprocess.run,
) -> dict[str, Any]:
    head = _head(repo)
    # CI evidence generators legitimately create untracked reports before this
    # gate runs. Source cleanliness means that no tracked candidate byte changed;
    # untracked evidence is bound by its own digest below.
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=no"):
        raise GateError("image subject requires a clean candidate checkout")
    try:
        completed = run(
            ["docker", "image", "inspect", image_ref],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        inspected = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as error:
        raise GateError("docker image inspection failed") from error
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise GateError("docker image inspection returned an unexpected result")
    image = inspected[0]
    digest = image.get("Id") if isinstance(image, dict) else None
    labels = (
        image.get("Config", {}).get("Labels", {}) if isinstance(image, dict) else {}
    )
    if not isinstance(digest, str) or not IMAGE_DIGEST.fullmatch(digest):
        raise GateError("image has no immutable sha256 ID")
    if (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != head
    ):
        raise GateError("image OCI revision label does not match candidate HEAD")
    sbom, sbom_sha = _stable_json_with_sha(sbom_path)
    scan, scan_sha = _stable_json_with_sha(scan_path)
    _validate_sbom_binding(sbom, image_ref=image_ref, image_digest=digest)
    _validate_scan_binding(scan, image_ref=image_ref, image_digest=digest)
    dockerfile = repo / "document-rag-platform/services/backend/Dockerfile"
    lockfile = repo / "document-rag-platform/services/backend/uv.lock"
    dockerfile_sha = sha256(dockerfile)
    lockfile_sha = sha256(lockfile)
    if _head(repo) != head or _git(
        repo, "status", "--porcelain=v1", "--untracked-files=no"
    ):
        raise GateError("candidate checkout changed during image subject creation")
    subject = {
        "schema_version": "1.0",
        "predicate_type": "context-vault/a11-image-provenance",
        "source_sha": head,
        "image_ref": image_ref,
        "image_digest": digest,
        "image_sbom_sha256": sbom_sha,
        "image_scan_sha256": scan_sha,
        "dockerfile_sha256": dockerfile_sha,
        "lockfile_sha256": lockfile_sha,
        "created_at_utc": _utc_now(),
        "build_secret_values_retained": False,
        "application_content_retained": False,
    }
    _write_private(output, subject)
    return subject


def _trivy_findings(scan: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    results = scan.get("Results")
    if not isinstance(results, list):
        raise GateError("Trivy report has no Results array")
    for result in results:
        vulnerabilities = (
            result.get("Vulnerabilities") if isinstance(result, dict) else None
        )
        if vulnerabilities is None:
            continue
        if not isinstance(vulnerabilities, list):
            raise GateError("Trivy vulnerabilities are malformed")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                raise GateError("Trivy vulnerability entry is malformed")
            severity = vulnerability.get("Severity")
            if severity in {"HIGH", "CRITICAL"}:
                findings.append(
                    {
                        "id": str(vulnerability.get("VulnerabilityID", "unknown")),
                        "severity": severity,
                    }
                )
    return findings


def verify_image_evidence(
    *,
    repo: Path,
    subject_path: Path,
    sbom_path: Path,
    scan_path: Path,
    signature_bundle: Path,
    certificate_identity: str,
    certificate_oidc_issuer: str,
    cosign_bin: str = "cosign",
    run: Run = subprocess.run,
) -> dict[str, Any]:
    subject, subject_sha = _stable_json_with_sha(subject_path)
    sbom, sbom_sha = _stable_json_with_sha(sbom_path)
    scan, scan_sha = _stable_json_with_sha(scan_path)
    _, signature_sha = _stable_json_with_sha(signature_bundle)
    stable_hashes = {
        "subject": subject_sha,
        "sbom": sbom_sha,
        "scan": scan_sha,
        "signature": signature_sha,
    }
    if (
        certificate_oidc_issuer != EXPECTED_OIDC_ISSUER
        or not EXPECTED_CERTIFICATE_IDENTITY.fullmatch(certificate_identity)
        or cosign_bin != "cosign"
    ):
        raise GateError("cosign verification authority is not the pinned CI workflow")
    head = _head(repo)
    _require_source_sha(subject.get("source_sha"), head)
    image_ref = subject.get("image_ref")
    image_digest = subject.get("image_digest")
    if (
        not isinstance(image_ref, str)
        or not image_ref
        or not isinstance(image_digest, str)
        or not IMAGE_DIGEST.fullmatch(image_digest)
    ):
        raise GateError("image subject digest is malformed")
    _validate_scan_binding(scan, image_ref=image_ref, image_digest=image_digest)
    _validate_sbom_binding(sbom, image_ref=image_ref, image_digest=image_digest)
    if subject.get("image_sbom_sha256") != stable_hashes["sbom"]:
        raise GateError("signed subject does not bind the image SBOM hash")
    if subject.get("image_scan_sha256") != stable_hashes["scan"]:
        raise GateError("signed subject does not bind the image scan hash")
    try:
        run(
            [
                cosign_bin,
                "verify-blob",
                "--bundle",
                str(signature_bundle),
                "--certificate-identity",
                certificate_identity,
                "--certificate-oidc-issuer",
                certificate_oidc_issuer,
                str(subject_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise GateError("cosign verification failed") from error
    current_hashes = {
        "subject": sha256(subject_path),
        "sbom": sha256(sbom_path),
        "scan": sha256(scan_path),
        "signature": sha256(signature_bundle),
    }
    if current_hashes != stable_hashes:
        raise GateError("image evidence changed during verification")
    findings = _trivy_findings(scan)
    return {
        "schema_version": "1.0",
        "receipt_type": "a11-image-security",
        "status": "PASS" if not findings else "FAIL",
        "source_sha": head,
        "image_digest": image_digest,
        "verified_at_utc": _utc_now(),
        "subject_sha256": stable_hashes["subject"],
        "sbom_sha256": stable_hashes["sbom"],
        "scan_sha256": stable_hashes["scan"],
        "signature_bundle_sha256": stable_hashes["signature"],
        "signature_identity": certificate_identity,
        "critical_high_findings": findings,
        "contains_application_content": False,
        "contains_credentials": False,
    }


def _receipt_status(payload: dict[str, Any]) -> bool:
    return payload.get("status") == "PASS"


def evaluate_release_candidate(
    *, repo: Path, manifest_path: Path, risks_path: Path
) -> dict[str, Any]:
    manifest_path = _confined_path(repo, manifest_path, label="release manifest")
    risks_path = _confined_path(repo, risks_path, label="known-risk register")
    canonical_risks = (
        repo.resolve(strict=True)
        / "document-rag-platform/infra/release/known-risks.json"
    ).resolve(strict=True)
    if risks_path != canonical_risks:
        raise GateError("release candidate must use the canonical known-risk register")
    manifest, manifest_sha = _stable_json_with_sha(manifest_path)
    risks, risks_sha = _stable_json_with_sha(risks_path)
    head = _head(repo)
    _require_source_sha(manifest.get("source_sha"), head)
    if manifest.get("schema_version") != "1.0" or risks.get("schema_version") != "1.0":
        raise GateError("unsupported release evidence schema")
    entries = manifest.get("gates")
    if not isinstance(entries, list):
        raise GateError("release candidate gates must be a list")
    by_name: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    image_digest: str | None = None
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise GateError("release gate entry is malformed")
        name = entry["name"]
        if name in by_name:
            raise GateError(f"duplicate release gate: {name}")
        relative = entry.get("receipt")
        expected_hash = entry.get("sha256")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(expected_hash, str)
            or not SHA256.fullmatch(expected_hash)
        ):
            raise GateError(f"unsafe receipt binding for gate: {name}")
        receipt_path = _repo_file(repo, relative, label=f"receipt for gate {name}")
        receipt, receipt_sha = _stable_json_with_sha(receipt_path)
        if receipt_sha != expected_hash:
            raise GateError(f"receipt hash mismatch for gate: {name}")
        _require_source_sha(receipt.get("source_sha"), head)
        if receipt.get("schema_version") != "1.0" or receipt.get(
            "receipt_type"
        ) != REQUIRED_RC_RECEIPT_TYPES.get(name):
            raise GateError(f"receipt type mismatch for gate: {name}")
        if not _receipt_status(receipt):
            failures.append(name)
        bound_digest = receipt.get("image_digest")
        if bound_digest is not None:
            if not isinstance(bound_digest, str) or not IMAGE_DIGEST.fullmatch(
                bound_digest
            ):
                raise GateError(f"malformed image digest in gate: {name}")
            if image_digest is not None and bound_digest != image_digest:
                raise GateError("release receipts bind different image digests")
            image_digest = bound_digest
        by_name[name] = entry
    if set(by_name) != REQUIRED_RC_GATES:
        missing = sorted(REQUIRED_RC_GATES - set(by_name))
        extra = sorted(set(by_name) - REQUIRED_RC_GATES)
        raise GateError(f"release gate set mismatch; missing={missing}, extra={extra}")
    if image_digest is None:
        raise GateError("release candidate has no immutable image digest")
    risk_entries = risks.get("risks")
    if not isinstance(risk_entries, list):
        raise GateError("known-risk register is malformed")
    blocking_risks: list[str] = []
    seen_risks: set[str] = set()
    for risk in risk_entries:
        if not isinstance(risk, dict):
            raise GateError("known risk must be an object")
        risk_id = risk.get("id")
        severity = risk.get("severity")
        status = risk.get("status")
        if (
            not isinstance(risk_id, str)
            or not risk_id
            or risk_id in seen_risks
            or severity not in {"P0", "P1", "P2", "P3"}
            or status not in {"OPEN", "MITIGATED", "ACCEPTED", "CLOSED"}
            or not isinstance(risk.get("owner"), str)
            or not risk["owner"].strip()
        ):
            raise GateError("known-risk entry is malformed")
        seen_risks.add(risk_id)
        if severity in {"P0", "P1"} and status != "CLOSED":
            blocking_risks.append(risk_id)
    clean = not _git(repo, "status", "--porcelain=v1")
    if not clean:
        failures.append("clean_worktree")
    if blocking_risks:
        failures.append("blocking_known_risks")
    failures = sorted(set(failures))
    return {
        "schema_version": "1.0",
        "receipt_type": "a11-release-candidate",
        "status": "PASS" if not failures else "FAIL",
        "source_sha": head,
        "image_digest": image_digest,
        "evaluated_at_utc": _utc_now(),
        "manifest_sha256": manifest_sha,
        "known_risks_sha256": risks_sha,
        "verified_gate_names": sorted(by_name),
        "blocking_known_risks": sorted(blocking_risks),
        "failures": failures,
        "remote_mutation_performed": False,
        "deployment_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    subcommands = parser.add_subparsers(dest="command", required=True)

    slo = subcommands.add_parser("slo")
    slo.add_argument("--policy", type=Path, required=True)
    slo.add_argument("--measurements", type=Path, required=True)
    slo.add_argument("--json-output", type=Path, required=True)

    subject = subcommands.add_parser("image-subject")
    subject.add_argument("--image-ref", required=True)
    subject.add_argument("--sbom", type=Path, required=True)
    subject.add_argument("--scan", type=Path, required=True)
    subject.add_argument("--json-output", type=Path, required=True)

    image = subcommands.add_parser("image-verify")
    image.add_argument("--subject", type=Path, required=True)
    image.add_argument("--sbom", type=Path, required=True)
    image.add_argument("--scan", type=Path, required=True)
    image.add_argument("--signature-bundle", type=Path, required=True)
    image.add_argument("--certificate-identity", required=True)
    image.add_argument(
        "--certificate-oidc-issuer",
        default="https://token.actions.githubusercontent.com",
    )
    image.add_argument("--cosign-bin", default="cosign")
    image.add_argument("--json-output", type=Path, required=True)

    release = subcommands.add_parser("rc")
    release.add_argument("--manifest", type=Path, required=True)
    release.add_argument("--known-risks", type=Path, required=True)
    release.add_argument("--json-output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "slo":
            result = evaluate_slo(
                repo=args.repo.resolve(),
                policy_path=args.policy,
                measurements_path=args.measurements,
            )
        elif args.command == "image-subject":
            result = create_image_subject(
                repo=args.repo.resolve(),
                image_ref=args.image_ref,
                sbom_path=args.sbom,
                scan_path=args.scan,
                output=args.json_output,
            )
            print(json.dumps(result, sort_keys=True))
            return 0
        elif args.command == "image-verify":
            result = verify_image_evidence(
                repo=args.repo.resolve(),
                subject_path=args.subject,
                sbom_path=args.sbom,
                scan_path=args.scan,
                signature_bundle=args.signature_bundle,
                certificate_identity=args.certificate_identity,
                certificate_oidc_issuer=args.certificate_oidc_issuer,
                cosign_bin=args.cosign_bin,
            )
        else:
            result = evaluate_release_candidate(
                repo=args.repo.resolve(),
                manifest_path=args.manifest,
                risks_path=args.known_risks,
            )
        _write_private(args.json_output, result)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    except GateError as error:
        print(f"A11 gate: FAIL ({error})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
