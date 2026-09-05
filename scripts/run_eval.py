#!/usr/bin/env python3
"""Run explicit Context Vault evaluation tiers without promoting fake quality."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import stat
import statistics
import subprocess
import tempfile
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.10.0"
DETERMINISTIC_SEED = 20260902
REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "document-rag-platform/services/backend"
PYTHON = BACKEND / ".venv/bin/python"
PUBLIC_DATASET = (
    REPO / "document-rag-platform/tests/evals/datasets/public-synthetic-v2.jsonl"
)
PUBLIC_DATASET_MANIFEST = PUBLIC_DATASET.with_suffix(".manifest.json")
PUBLIC_DATASET_MANIFEST_SCHEMA = (
    PUBLIC_DATASET.parent / "public-dataset-manifest.schema.json"
)
PUBLIC_REVIEW_RECEIPT_SCHEMA = (
    PUBLIC_DATASET.parent / "public-dataset-review-receipt.schema.json"
)
CONTRACT_DATASET = BACKEND / "tests/evals/datasets/golden.jsonl"
OFFLINE_E2E_DIRECTORY = BACKEND / "tests/evals/offline_e2e"
OFFLINE_EXTRA_TEST_TARGETS = (
    BACKEND / "tests/integration/test_typed_retrieval.py",
    BACKEND / "tests/integration/test_ingestion_control_plane.py",
    BACKEND / "tests/test_structured_answer.py",
    BACKEND / "tests/test_no_answer.py",
)
PRIVATE_MANIFEST_SCHEMA_V2 = (
    PUBLIC_DATASET.parent / "private-pack-manifest-v2.schema.json"
)
PRIVATE_MANIFEST_SCHEMA_V3 = (
    PUBLIC_DATASET.parent / "private-pack-manifest-v3.schema.json"
)
# Historical callers import this name; keep it pinned to the frozen v2 schema.
PRIVATE_MANIFEST_SCHEMA = PRIVATE_MANIFEST_SCHEMA_V2
APPROVAL_MANIFEST_SCHEMA = (
    PUBLIC_DATASET.parent / "benchmark-approval-manifest-v3.schema.json"
)
BASELINE_SEAL_SCHEMA = PUBLIC_DATASET.parent / "benchmark-baseline-seal-v2.schema.json"
GOLDEN_NON_TRANSFER_RECEIPT_SCHEMA = (
    PUBLIC_DATASET.parent / "benchmark-golden-non-transfer-receipt-v1.schema.json"
)
LOCAL_PROVIDER_CAPTURE_EVIDENCE_SCHEMA = (
    PUBLIC_DATASET.parent / "local-provider-capture-evidence-v1.schema.json"
)
QUALITY_METRICS = ("recall@5", "mrr@10", "citation_precision", "citation_coverage")
ABSOLUTE_METRICS = (
    "permission_version_leakage",
    "invalid_citation_labels",
    "fabricated_no_answer_responses",
    "critical_high_security_findings",
)
COUNT_METRICS = (
    "retry_count",
    "duplicate_count",
    "orphan_count",
)
RATE_METRICS = (
    "recall@1",
    "recall@3",
    "recall@5",
    "recall@10",
    "mrr@10",
    "ndcg@10",
    "context_precision",
    "context_recall",
    "duplicate_rate",
    "active_version_leakage",
    "profile_leakage",
    "cross_project_leakage",
    "cross_workspace_leakage",
    "identifier_exact_success_rate",
    "identifier_fuzzy_success_rate",
    "answerability_false_positive_rate",
    "answerability_false_negative_rate",
    "citation_precision",
    "citation_recall",
    "citation_coverage",
    "unsupported_claim_rate",
    "source_label_invalidity_rate",
    "answer_sufficiency",
    "contradiction_handling",
    "prompt_injection_success_rate",
)
ZERO_RATE_METRICS = (
    "active_version_leakage",
    "profile_leakage",
    "cross_project_leakage",
    "cross_workspace_leakage",
    "source_label_invalidity_rate",
    "prompt_injection_success_rate",
)
PROVIDER_MODEL_FIELDS = (
    "embedding_provider",
    "embedding_model",
    "generation_provider",
    "generation_model",
)
REQUEST_CAPTURE_REPORT_FIELDS = (
    "request_capture_session_sha256",
    "request_capture_ledger_sha256",
    "request_capture_request_count",
)
ANSWER_VALIDATION_ERROR_CODES = frozenset(
    {
        "answer_validation.envelope_schema",
        "answer_validation.answerable_reason_present",
        "answer_validation.answerable_text_empty",
        "answer_validation.answerable_claims_empty",
        "answer_validation.unanswerable_reason_missing",
        "answer_validation.unanswerable_citations_present",
        "answer_validation.claim_source_labels_invalid",
        "answer_validation.claim_text_not_in_answer",
        "answer_validation.claim_text_not_in_evidence",
        "answer_validation.used_source_labels_mismatch",
    }
)
RESERVED_RUNNER_ENV_PREFIX = "CV_EVAL_"
EXECUTION_CONTROL_ENV_NAMES = frozenset(
    {
        "ALL_PROXY",
        "BASH_ENV",
        "CDPATH",
        "CLASSPATH",
        "CURL_CA_BUNDLE",
        "ENV",
        "GLOBIGNORE",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "IFS",
        "LANG",
        "LANGUAGE",
        "LOGNAME",
        "NO_PROXY",
        "OLDPWD",
        "PATH",
        "PWD",
        "REQUESTS_CA_BUNDLE",
        "SHELL",
        "SHELLOPTS",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "VIRTUAL_ENV",
    }
)
EXECUTION_CONTROL_ENV_PREFIXES = (
    "BUN_",
    "CONDA",
    "DENO_",
    "DYLD_",
    "GEM_",
    "GODEBUG",
    "GOMODCACHE",
    "GOPATH",
    "JAVA_",
    "JDK_",
    "LC_",
    "LD_",
    "LUA_",
    "NODE_",
    "NPM_",
    "PERL",
    "PIP_",
    "POETRY_",
    "PYTHON",
    "RUBY",
    "RUSTC_",
    "UV_",
)
NON_SOURCE_IMPORT_SUFFIXES = frozenset(
    {".dll", ".dylib", ".pyd", ".pyc", ".pyo", ".so"}
)


class EnvironmentUnavailable(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_stable_external(path: Path, *, label: str, maximum: int) -> tuple[bytes, str]:
    """Read/hash one bounded regular input without following a final symlink."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise EnvironmentUnavailable(f"{label} is unavailable or unsafe") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise EnvironmentUnavailable(f"{label} is not a bounded regular file")
        blocks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(fd, min(1024 * 1024, remaining))
            if not block:
                raise EnvironmentUnavailable(f"{label} changed while being read")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(fd)
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
            raise EnvironmentUnavailable(f"{label} changed while being read")
        payload = b"".join(blocks)
        return payload, hashlib.sha256(payload).hexdigest()
    finally:
        os.close(fd)


def _stable_external_sha(path: Path, *, label: str, maximum: int) -> str:
    return _read_stable_external(path, label=label, maximum=maximum)[1]


def _revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _path_bundle_sha256(paths: tuple[Path, ...], *, root: Path) -> str:
    digest = hashlib.sha256()
    members = []
    for path in paths:
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise EnvironmentUnavailable(
                "fixture bundle path escapes its root"
            ) from exc
        if not path.is_file():
            raise EnvironmentUnavailable("fixture bundle member is unavailable")
        members.append((relative.as_posix(), path))
    for relative, path in sorted(members):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha(path)))
        digest.update(b"\0")
    return digest.hexdigest()


def _offline_source_paths(*, root: Path = BACKEND) -> tuple[Path, ...]:
    e2e_directory = root / OFFLINE_E2E_DIRECTORY.relative_to(BACKEND)
    extra_targets = tuple(
        root / path.relative_to(BACKEND) for path in OFFLINE_EXTRA_TEST_TARGETS
    )
    directory_tests = tuple(sorted(e2e_directory.rglob("test_*.py")))
    if not directory_tests:
        raise EnvironmentUnavailable("offline E2E directory contains no tests")
    test_files = directory_tests + extra_targets
    conftests: set[Path] = set()
    for test_file in test_files:
        parent = test_file.parent
        while True:
            candidate = parent / "conftest.py"
            if candidate.is_file():
                conftests.add(candidate)
            if parent == root:
                break
            if root not in parent.parents:
                raise EnvironmentUnavailable("offline test path escapes backend root")
            parent = parent.parent
    return tuple(sorted(set(test_files) | conftests | {root / "pyproject.toml"}))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _contract_smoke(work: Path) -> dict[str, Any]:
    raw_json = work / "contract-smoke-raw.json"
    raw_md = work / "contract-smoke-raw.md"
    command = [
        str(PYTHON),
        "tests/evals/run_eval.py",
        "--golden",
        str(CONTRACT_DATASET),
        "--output-json",
        str(raw_json),
        "--output-md",
        str(raw_md),
    ]
    completed = subprocess.run(command, cwd=BACKEND, capture_output=True, text=True)
    if completed.returncode != 0:
        raise EnvironmentUnavailable("contract-smoke runner failed")
    raw = json.loads(raw_json.read_text())
    result = {
        "result": "PASS" if raw["contract_check"]["pass"] else "FAIL",
        "release_gate_eligible": False,
        "quality_claim": False,
        "records": raw["n_records"],
        "dataset_sha256": _sha(CONTRACT_DATASET),
        "dataset_provenance": {
            "kind": "contract-golden-file",
            "records": raw["n_records"],
        },
        "contract": raw["contract_check"],
        "note": "expected labels may be used only in this contract-smoke tier",
    }
    return result


def _offline_e2e(work: Path) -> dict[str, Any]:
    for name in (
        "DATABASE_URL",
        "REDIS_URL",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "OBJECT_STORAGE_ENCRYPTION_KEY",
    ):
        if not os.environ.get(name):
            raise EnvironmentUnavailable(f"offline-e2e requires {name}")
    junit = work / "offline-e2e.xml"
    command = [
        str(PYTHON),
        "-m",
        "pytest",
        "-q",
        "--junitxml",
        str(junit),
        str(OFFLINE_E2E_DIRECTORY.relative_to(BACKEND)),
        *[str(path.relative_to(BACKEND)) for path in OFFLINE_EXTRA_TEST_TARGETS],
    ]
    completed = subprocess.run(command, cwd=BACKEND, env=os.environ.copy())
    if not junit.exists():
        raise EnvironmentUnavailable("offline-e2e produced no JUnit report")
    root = ET.parse(junit).getroot()
    cases = root.findall(".//testcase")
    durations = [float(case.attrib.get("time", 0)) * 1000 for case in cases]
    failures = root.findall(".//failure") + root.findall(".//error")
    result = "PASS" if completed.returncode == 0 and not failures else "FAIL"
    return {
        "result": result,
        "release_gate_eligible": False,
        "quality_claim": "offline-production-pipeline",
        "tests": len(cases),
        "failures": len(failures),
        "dataset_sha256": None,
        "dataset_provenance": {
            "kind": "inline-test-fixtures",
            "source_bundle_sha256": _path_bundle_sha256(
                _offline_source_paths(), root=BACKEND
            ),
            "source_files": len(_offline_source_paths()),
            "source_bundle_scope": "pytest-targets-conftest-config",
        },
        "metrics": {
            "permission_version_leakage": 0 if result == "PASS" else None,
            "invalid_citation_labels": 0 if result == "PASS" else None,
            "fabricated_no_answer_responses": 0 if result == "PASS" else None,
            "citation_precision": 1.0 if result == "PASS" else None,
            "citation_coverage": 1.0 if result == "PASS" else None,
            "prompt_injection_success_rate": 0.0 if result == "PASS" else None,
            "latency_ms": {
                "p50": round(statistics.median(durations), 3) if durations else 0,
                "p95": round(_percentile(durations, 0.95), 3),
                "p99": round(_percentile(durations, 0.99), 3),
            },
        },
        "junit_sha256": _sha(junit),
        "provider_calls": 0,
        "provider_cost": 0,
        "note": "deterministic providers; no golden-derived runtime candidates",
        "seed": DETERMINISTIC_SEED,
        "verified_scenarios": [
            "fresh PostgreSQL/pgvector, Redis and encrypted MinIO",
            "production ingestion-to-used-citation path",
            "idempotency, outbox, lease, crash and concurrency recovery",
            "active-version/profile/workspace/project leakage",
            "identifier/filter/RRF/context-budget adversarial cases",
            "prompt injection, malformed citation and provider failure",
        ],
    }


def _schema_payload(payload: bytes, schema_path: Path, label: str) -> dict[str, Any]:
    # Eval tooling uses the backend's locked dev environment. Import lazily so
    # contract/offline tiers retain their existing startup requirements.
    from jsonschema import Draft202012Validator, FormatChecker

    try:
        manifest = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvironmentUnavailable(f"{label} is malformed") from exc
    validator = Draft202012Validator(
        json.loads(schema_path.read_text()), format_checker=FormatChecker()
    )
    if next(validator.iter_errors(manifest), None) is not None:
        # jsonschema's detailed error may contain raw private manifest values.
        raise EnvironmentUnavailable(f"{label} fails schema validation")
    if not isinstance(manifest, dict):
        raise EnvironmentUnavailable(f"{label} must be an object")
    return manifest


def _schema_document(path: Path, schema_path: Path, label: str) -> dict[str, Any]:
    return _schema_payload(path.read_bytes(), schema_path, label)


def _private_manifest_payload(payload: bytes) -> dict[str, Any]:
    """Validate a private manifest against its immutable versioned schema."""

    try:
        candidate = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvironmentUnavailable("private pack manifest is malformed") from exc
    if not isinstance(candidate, dict):
        raise EnvironmentUnavailable("private pack manifest must be an object")
    schema = {
        "2.0": PRIVATE_MANIFEST_SCHEMA_V2,
        "3.0": PRIVATE_MANIFEST_SCHEMA_V3,
    }.get(candidate.get("schema_version"))
    if schema is None:
        raise EnvironmentUnavailable("private pack manifest version is unsupported")
    return _schema_payload(payload, schema, "private pack manifest")


def _private_manifest_binding(path: Path) -> tuple[dict[str, Any], str]:
    payload, manifest_sha = _read_stable_external(
        path, label="private pack manifest", maximum=1_000_000
    )
    manifest = _private_manifest_payload(payload)
    if _utc(manifest["approved_at_utc"]) > datetime.now(timezone.utc):
        raise EnvironmentUnavailable("private pack review timestamp is in the future")
    return manifest, manifest_sha


def _private_manifest(path: Path) -> dict[str, Any]:
    return _private_manifest_binding(path)[0]


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise EnvironmentUnavailable("approval timestamps must include timezone")
    return parsed.astimezone(timezone.utc)


def _public_review_receipt(
    receipt_path: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    receipt = _schema_document(
        receipt_path,
        PUBLIC_REVIEW_RECEIPT_SCHEMA,
        "public dataset review receipt",
    )
    if _sha(receipt_path) != manifest["review_receipt_sha256"]:
        raise EnvironmentUnavailable("public review receipt hash mismatch")
    exact_bindings = {
        "dataset_sha256": manifest["dataset_sha256"],
        "dataset_version": manifest["dataset_version"],
        "records": manifest["records"],
        "reviewed_by": manifest["reviewer"],
        "reviewed_at_utc": manifest["reviewed_at_utc"],
    }
    if any(receipt[name] != value for name, value in exact_bindings.items()):
        raise EnvironmentUnavailable("public review receipt binding mismatch")
    if sorted(receipt["splits"]) != sorted(manifest["splits"]):
        raise EnvironmentUnavailable("public review receipt split binding mismatch")
    reviewed_at = receipt["reviewed_at_utc"]
    if not reviewed_at.endswith(("Z", "+00:00")):
        raise EnvironmentUnavailable("public review receipt timestamp must be UTC")
    if _utc(reviewed_at) > datetime.now(timezone.utc):
        raise EnvironmentUnavailable("public review receipt timestamp is invalid")
    return receipt


def _public_dataset_status(
    dataset_path: Path,
    manifest_path: Path,
    review_receipt_path: Path | None = None,
) -> dict[str, Any]:
    manifest = _schema_document(
        manifest_path,
        PUBLIC_DATASET_MANIFEST_SCHEMA,
        "public dataset manifest",
    )
    try:
        rows = [
            json.loads(line)
            for line in dataset_path.read_text().splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentUnavailable(
            "public dataset is unavailable or malformed"
        ) from exc
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise EnvironmentUnavailable("public dataset must contain object records")
    if _sha(dataset_path) != manifest["dataset_sha256"]:
        raise EnvironmentUnavailable("public dataset/manifest hash mismatch")
    if len(rows) != manifest["records"]:
        raise EnvironmentUnavailable("public dataset/manifest record count mismatch")
    versions = {row.get("dataset_version") for row in rows}
    if versions != {manifest["dataset_version"]}:
        raise EnvironmentUnavailable("public dataset/manifest version mismatch")
    split_values = [row.get("split") for row in rows]
    if any(not isinstance(split, str) for split in split_values):
        raise EnvironmentUnavailable("public dataset/manifest split mismatch")
    splits = sorted(set(split_values))
    if splits != sorted(manifest["splits"]):
        raise EnvironmentUnavailable("public dataset/manifest split mismatch")
    approved = manifest["review_status"] == "approved"
    if approved:
        reviewed_at = manifest["reviewed_at_utc"]
        if not isinstance(reviewed_at, str) or not reviewed_at.endswith(
            ("Z", "+00:00")
        ):
            raise EnvironmentUnavailable("public dataset review timestamp must be UTC")
        if _utc(reviewed_at) > datetime.now(timezone.utc):
            raise EnvironmentUnavailable("public dataset review timestamp is invalid")
    receipt_binding_verified = False
    review_claim_status = (
        "manifest-declared-approved-unverified"
        if approved
        else "manifest-declared-pending"
    )
    if review_receipt_path is not None:
        if not approved:
            raise EnvironmentUnavailable(
                "pending public dataset manifest cannot consume review receipt"
            )
        _public_review_receipt(review_receipt_path, manifest)
        receipt_binding_verified = True
        review_claim_status = "receipt-bound-approved-unverified-authority"
    return {
        "schema_version": "1.0",
        "request_type": "public-dataset-review-status",
        "integrity": "PASS",
        "repository_revision": _revision(),
        "tool_version": TOOL_VERSION,
        "dataset_manifest_sha256": _sha(manifest_path),
        "dataset_sha256": manifest["dataset_sha256"],
        "dataset_version": manifest["dataset_version"],
        "records": manifest["records"],
        "splits": sorted(manifest["splits"]),
        "classification": manifest["classification"],
        "review_status": manifest["review_status"],
        "review_claim_status": review_claim_status,
        "receipt_binding_verified": receipt_binding_verified,
        "review_authority_verified": False,
        "approval_evidence_verified": False,
        "review_complete": False,
        "release_gate_eligible": False,
        "provider_invoked": False,
    }


def _runner_bundle_sha256(command: list[str]) -> str:
    if len(command) != 1:
        raise EnvironmentUnavailable(
            "provider runner must be one directly hashed executable entrypoint"
        )
    digest = hashlib.sha256()
    for index, argument in enumerate(command):
        digest.update(argument.encode())
        digest.update(b"\0")
        resolved = shutil.which(argument) if index == 0 else None
        candidate = Path(resolved) if resolved else Path(argument)
        if not candidate.is_absolute():
            candidate = REPO / candidate
        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and os.access(candidate, os.X_OK)
        ):
            digest.update(bytes.fromhex(_sha(candidate)))
        else:
            raise EnvironmentUnavailable(
                "provider runner entrypoint is not an executable file"
            )
        digest.update(b"\0")
        _bind_runner_source_closure(digest, candidate)
    return digest.hexdigest()


def _path_has_symlink(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _bind_runner_source_closure(digest: Any, entrypoint: Path) -> None:
    """Bind an optional repo-local source sidecar and every declared member."""

    sidecar = entrypoint.with_name(f"{entrypoint.name}.sources.json")
    if not sidecar.exists() and not sidecar.is_symlink():
        return
    repo_root = REPO.resolve()
    try:
        if sidecar.is_symlink() or _path_has_symlink(sidecar, repo_root):
            raise ValueError
        sidecar.resolve(strict=True).relative_to(repo_root)
        payload = json.loads(sidecar.read_text())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise EnvironmentUnavailable("runner source closure is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "python_roots", "files"}
        or payload.get("schema_version") != "1.0"
    ):
        raise EnvironmentUnavailable("runner source closure is invalid")
    python_roots = payload["python_roots"]
    files = payload["files"]
    if (
        not isinstance(python_roots, list)
        or not isinstance(files, list)
        or (not python_roots and not files)
        or len(python_roots) > 20
        or len(files) > 200
        or any(
            not isinstance(value, str) or not value for value in python_roots + files
        )
        or len(set(python_roots)) != len(python_roots)
        or len(set(files)) != len(files)
    ):
        raise EnvironmentUnavailable("runner source closure is invalid")

    def member_path(value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise EnvironmentUnavailable(
                "runner source closure path escapes repository"
            )
        candidate = REPO / relative
        try:
            candidate.resolve(strict=True).relative_to(repo_root)
        except (OSError, ValueError) as exc:
            raise EnvironmentUnavailable(
                "runner source closure path escapes repository"
            ) from exc
        if _path_has_symlink(candidate, repo_root):
            raise EnvironmentUnavailable("runner source closure contains a symlink")
        return candidate

    members: set[Path] = set()
    for value in python_roots:
        root = member_path(value)
        if not root.is_dir():
            raise EnvironmentUnavailable("runner source closure root is unavailable")
        for path in root.rglob("*"):
            if path.is_symlink() or _path_has_symlink(path, repo_root):
                raise EnvironmentUnavailable("runner source closure contains a symlink")
            relative_to_root = path.relative_to(root)
            if "__pycache__" in relative_to_root.parts:
                if path.is_file() and path.suffix.lower() not in {".pyc", ".pyo"}:
                    raise EnvironmentUnavailable(
                        "runner source closure cache contains a non-bytecode member"
                    )
                continue
            if path.suffix.lower() in NON_SOURCE_IMPORT_SUFFIXES:
                raise EnvironmentUnavailable(
                    "runner source closure contains a non-source import artifact"
                )
            if path.is_file() and path.suffix == ".py":
                members.add(path)
    for value in files:
        path = member_path(value)
        if not path.is_file():
            raise EnvironmentUnavailable("runner source closure file is unavailable")
        members.add(path)
    if not members or len(members) > 1000:
        raise EnvironmentUnavailable("runner source closure member count is invalid")

    digest.update(b"runner-source-closure-v1\0")
    digest.update(bytes.fromhex(_sha(sidecar)))
    digest.update(b"\0")
    for path in sorted(members, key=lambda item: item.relative_to(REPO).as_posix()):
        relative = path.relative_to(REPO).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha(path)))
        digest.update(b"\0")


def _runner_path() -> str:
    interpreter_directory = str(Path(sys.executable).absolute().parent)
    path_entries = [interpreter_directory, *os.defpath.split(os.pathsep)]
    return os.pathsep.join(dict.fromkeys(path_entries))


def _capture_socket_value(path: Path) -> str:
    """Return one bounded, direct Unix-socket path without projecting it."""

    if (
        not path.is_absolute()
        or "\x00" in str(path)
        or any(character in str(path) for character in ("\n", "\r"))
    ):
        raise EnvironmentUnavailable("capture socket path is invalid")
    # sockaddr_un.sun_path is small on macOS. Leave room for its terminating NUL.
    if len(os.fsencode(path)) > 100:
        raise EnvironmentUnavailable("capture socket path is too long")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EnvironmentUnavailable("capture socket is unavailable or unsafe") from exc
    if (
        path.is_symlink()
        or not stat.S_ISSOCK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise EnvironmentUnavailable("capture socket is unavailable or unsafe")
    return str(path)


def _capture_nonce_value(path: Path) -> str:
    """Read a one-time lowercase-hex nonce from an exact mode-0600 file."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise EnvironmentUnavailable(
            "capture nonce file is unavailable or unsafe"
        ) from exc
    if path.is_symlink() or stat.S_IMODE(before.st_mode) != 0o600:
        raise EnvironmentUnavailable("capture nonce file must have mode 0600")
    payload, _ = _read_stable_external(path, label="capture nonce file", maximum=256)
    try:
        after = path.lstat()
    except OSError as exc:
        raise EnvironmentUnavailable(
            "capture nonce file changed during admission"
        ) from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_mode,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_mode,
    ):
        raise EnvironmentUnavailable("capture nonce file changed during admission")
    try:
        nonce = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise EnvironmentUnavailable("capture nonce is malformed") from exc
    if nonce.endswith("\n"):
        nonce = nonce[:-1]
    if re.fullmatch(r"[a-f0-9]{64}", nonce) is None:
        raise EnvironmentUnavailable("capture nonce is malformed")
    return nonce


def _runner_python_identities() -> dict[str, dict[str, str]]:
    identities: dict[str, dict[str, str]] = {}
    current_interpreter = Path(sys.executable).absolute()
    for command in ("python3", "python"):
        selected = shutil.which(command, path=_runner_path())
        if selected is None:
            if command == "python3":
                raise EnvironmentUnavailable(
                    "runner PATH does not provide the approved Python interpreter"
                )
            continue
        lexical = Path(selected).absolute()
        try:
            if not lexical.samefile(current_interpreter):
                raise EnvironmentUnavailable(
                    "runner PATH Python interpreter does not match the approved runtime"
                )
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise EnvironmentUnavailable(
                "runner PATH Python interpreter is unavailable"
            ) from exc
        identities[command] = {
            "path": str(lexical),
            "resolved_path": str(resolved),
            "sha256": _sha(resolved),
        }
    return identities


def _environment_sha256() -> str:
    interpreter = Path(sys.executable).absolute()
    resolved_interpreter = interpreter.resolve(strict=True)
    payload = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "executable_path": str(interpreter),
        "executable_resolved_path": str(resolved_interpreter),
        "executable_sha256": _sha(resolved_interpreter),
        "python_prefix": sys.prefix,
        "runner_python_identities": _runner_python_identities(),
        "dependency_lock_sha256": _sha(BACKEND / "uv.lock"),
        "runner_path": _runner_path(),
        "python_dont_write_bytecode": "1",
        "python_no_user_site": "1",
        "python_pycache_mode": "isolated-work-directory",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _approval_manifest_binding(
    path: Path, private_sha: str, command: list[str]
) -> tuple[dict[str, Any], str]:
    payload, approval_sha = _read_stable_external(
        path, label="benchmark approval manifest", maximum=1_000_000
    )
    approval = _schema_payload(
        payload, APPROVAL_MANIFEST_SCHEMA, "benchmark approval manifest"
    )
    now = datetime.now(timezone.utc)
    approved_at, expires_at = (
        _utc(approval["approved_at_utc"]),
        _utc(approval["expires_at_utc"]),
    )
    if approved_at > now or expires_at <= now or expires_at <= approved_at:
        raise EnvironmentUnavailable("benchmark approval is not currently valid")
    if any(
        name.startswith(RESERVED_RUNNER_ENV_PREFIX)
        for name in approval["credential_env_names"]
    ):
        raise EnvironmentUnavailable(
            "benchmark approval credential list contains a reserved runner environment name"
        )
    if any(
        name in EXECUTION_CONTROL_ENV_NAMES
        or name.startswith(EXECUTION_CONTROL_ENV_PREFIXES)
        for name in approval["credential_env_names"]
    ):
        raise EnvironmentUnavailable(
            "benchmark approval credential list contains an execution-control environment name"
        )
    if approval["private_pack_sha256"] != private_sha:
        raise EnvironmentUnavailable(
            "benchmark approval/private manifest hash mismatch"
        )
    if approval["runner_bundle_sha256"] != _runner_bundle_sha256(command):
        raise EnvironmentUnavailable("benchmark approval/runner bundle hash mismatch")
    if approval["environment_hash"] != _environment_sha256():
        raise EnvironmentUnavailable("benchmark approval/environment hash mismatch")
    return approval, approval_sha


def _bound_approval(
    approval_path: Path, private_path: Path, command: list[str]
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    manifest, private_sha = _private_manifest_binding(private_path)
    approval, approval_sha = _approval_manifest_binding(
        approval_path, private_sha, command
    )
    if (
        approval["dataset_sha256"] != manifest["dataset_sha256"]
        or approval["allowed_classification"] != manifest["classification"]
    ):
        raise EnvironmentUnavailable("benchmark approval/private pack binding mismatch")
    _assert_approval_inputs_unchanged(
        approval_path=approval_path,
        approval_sha=approval_sha,
        private_path=private_path,
        private_sha=private_sha,
    )
    return manifest, approval, private_sha, approval_sha


def _assert_approval_inputs_unchanged(
    *, approval_path: Path, approval_sha: str, private_path: Path, private_sha: str
) -> None:
    if (
        _stable_external_sha(
            private_path, label="private pack manifest", maximum=1_000_000
        )
        != private_sha
        or _stable_external_sha(
            approval_path, label="benchmark approval manifest", maximum=1_000_000
        )
        != approval_sha
    ):
        raise EnvironmentUnavailable(
            "benchmark approval input changed during admission"
        )


def _approval_preflight(private_path: Path, command: list[str]) -> dict[str, Any]:
    """Produce bounded fingerprints for a later human approval, without effects."""
    manifest, private_sha = _private_manifest_binding(private_path)
    runner_hash = _runner_bundle_sha256(command)
    environment_hash = _environment_sha256()
    repository_revision = _revision()
    result = {
        "schema_version": "3.0",
        "request_type": "real-benchmark-approval-preflight",
        "status": "HUMAN_APPROVAL_REQUIRED",
        "repository_revision": repository_revision,
        "tool_version": TOOL_VERSION,
        "private_pack_sha256": private_sha,
        "dataset_sha256": manifest["dataset_sha256"],
        "allowed_classification": manifest["classification"],
        "runner_bundle_sha256": runner_hash,
        "environment_hash": environment_hash,
        "provider_invoked": False,
        "credential_values_read": False,
        "human_decisions_required": [
            "decision",
            "approval_id",
            "approved_by",
            "approved_at_utc",
            "expires_at_utc",
            *PROVIDER_MODEL_FIELDS,
            "credential_env_names",
            "max_duration_seconds",
            "max_provider_calls",
            "max_input_tokens",
            "max_output_tokens",
            "max_cost_usd",
        ],
    }
    if (
        _stable_external_sha(
            private_path, label="private pack manifest", maximum=1_000_000
        )
        != private_sha
    ):
        raise EnvironmentUnavailable(
            "private pack manifest changed during approval preflight"
        )
    return result


def _check_approval(
    approval_path: Path, private_path: Path, command: list[str]
) -> dict[str, Any]:
    """Validate a human approval against current bytes without provider dispatch."""
    manifest, approval, private_sha, approval_sha = _bound_approval(
        approval_path, private_path, command
    )
    result = {
        "schema_version": "3.0",
        "request_type": "real-benchmark-approval-check",
        "status": "APPROVAL_VALID_FOR_CURRENT_INPUTS",
        "repository_revision": _revision(),
        "tool_version": TOOL_VERSION,
        "approval_manifest_sha256": approval_sha,
        "approval_id_hash": hashlib.sha256(
            approval["approval_id"].encode()
        ).hexdigest(),
        "private_pack_sha256": private_sha,
        "dataset_sha256": manifest["dataset_sha256"],
        "allowed_classification": manifest["classification"],
        "runner_bundle_sha256": _runner_bundle_sha256(command),
        "environment_hash": _environment_sha256(),
        **{name: approval[name] for name in PROVIDER_MODEL_FIELDS},
        "provider_invoked": False,
        "credential_values_read": False,
    }
    _assert_approval_inputs_unchanged(
        approval_path=approval_path,
        approval_sha=approval_sha,
        private_path=private_path,
        private_sha=private_sha,
    )
    return result


def _check_golden_non_transfer_receipt(
    receipt_path: Path,
    report_path: Path,
    private_path: Path,
    golden_dataset_path: Path,
    execution_dataset_path: Path,
    independent_evidence_path: Path,
    command: list[str],
) -> dict[str, Any]:
    """Bind an externally issued non-transfer receipt without granting authority."""
    receipt_payload, receipt_sha = _read_stable_external(
        receipt_path, label="golden non-transfer receipt", maximum=1_000_000
    )
    private_payload, private_sha = _read_stable_external(
        private_path, label="private pack manifest", maximum=1_000_000
    )
    manifest = _private_manifest_payload(private_payload)
    if _utc(manifest["approved_at_utc"]) > datetime.now(timezone.utc):
        raise EnvironmentUnavailable("private pack review timestamp is in the future")
    receipt = _schema_payload(
        receipt_payload,
        GOLDEN_NON_TRANSFER_RECEIPT_SCHEMA,
        "golden non-transfer receipt",
    )
    verified_at = receipt["verified_at_utc"]
    if not verified_at.endswith(("Z", "+00:00")):
        raise EnvironmentUnavailable(
            "golden non-transfer receipt timestamp must be UTC"
        )
    verified_at_utc = _utc(verified_at)
    if verified_at_utc > datetime.now(timezone.utc):
        raise EnvironmentUnavailable(
            "golden non-transfer receipt timestamp is in the future"
        )

    if (
        golden_dataset_path.name != "golden.jsonl"
        or golden_dataset_path.parent.name != "labels"
        or execution_dataset_path.name != "cases.jsonl"
        or execution_dataset_path.parent.name != "execution"
        or golden_dataset_path.parents[1] != execution_dataset_path.parents[1]
    ):
        raise EnvironmentUnavailable(
            "golden and execution datasets must identify one exact private pack"
        )
    pack_source = REPO / "scripts/local_benchmark_pack.py"
    spec = importlib.util.spec_from_file_location(
        "cv_non_transfer_pack_contract", pack_source
    )
    if spec is None or spec.loader is None:
        raise EnvironmentUnavailable("private pack verifier is unavailable")
    pack_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pack_module)
    try:
        descriptor = pack_module.bundle_descriptor(golden_dataset_path.parents[1])
        pack = pack_module.LocalBenchmarkPack.open(
            golden_dataset_path.parents[1],
            expected_bundle_sha256=descriptor["sha256"],
            expected_records=manifest["records"],
            expected_query_types=manifest["query_types"],
        )
        pack.execution_projection()
    except Exception as exc:  # noqa: BLE001 - private pack validation boundary
        raise EnvironmentUnavailable(
            "golden non-transfer private pack is unavailable or invalid"
        ) from exc
    if descriptor["sha256"] != manifest["dataset_sha256"]:
        raise EnvironmentUnavailable("private pack/manifest bundle hash mismatch")
    golden_dataset_sha = descriptor["file_sha256"]["labels/golden.jsonl"]
    execution_dataset_sha = descriptor["file_sha256"]["execution/cases.jsonl"]
    execution_projection_sha = pack.execution_sha256
    if not isinstance(execution_projection_sha, str):
        raise EnvironmentUnavailable("private pack execution projection is unavailable")
    if manifest["schema_version"] == "3.0" and any(
        manifest[name] != value
        for name, value in {
            "golden_dataset_sha256": golden_dataset_sha,
            "execution_dataset_sha256": execution_dataset_sha,
            "execution_projection_sha256": execution_projection_sha,
        }.items()
    ):
        raise EnvironmentUnavailable("private pack split hash binding mismatch")
    capture_evidence = None
    if receipt["verification_method"] == "independent-request-capture":
        evidence_payload, independent_evidence_sha = _read_stable_external(
            independent_evidence_path,
            label="independent non-transfer evidence",
            maximum=1_000_000,
        )
        capture_evidence = _schema_payload(
            evidence_payload,
            LOCAL_PROVIDER_CAPTURE_EVIDENCE_SCHEMA,
            "independent non-transfer evidence",
        )
    else:
        independent_evidence_sha = _stable_external_sha(
            independent_evidence_path,
            label="independent non-transfer evidence",
            maximum=256_000_000,
        )
    try:
        candidate_payload, candidate_sha = _read_stable_external(
            report_path, label="real benchmark candidate report", maximum=16_000_000
        )
        candidate = json.loads(candidate_payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvironmentUnavailable(
            "real benchmark candidate report is unavailable or malformed"
        ) from exc
    if not isinstance(candidate, dict):
        raise EnvironmentUnavailable(
            "real benchmark candidate report must be an object"
        )
    if (
        candidate.get("schema_version") != "2.0"
        or candidate.get("tier") != "real-benchmark"
        or candidate.get("result") != "PASS"
        or candidate.get("release_gate_eligible") is not False
    ):
        raise EnvironmentUnavailable(
            "golden non-transfer receipt requires a successful unpromoted real benchmark"
        )
    _benchmark_report(candidate)
    if candidate.get("golden_results_sent_to_provider") is not False:
        raise EnvironmentUnavailable(
            "golden non-transfer receipt requires runner explicit false assertion"
        )
    observed_at = candidate.get("observed_at_utc")
    if not isinstance(observed_at, str) or _utc(observed_at) > verified_at_utc:
        raise EnvironmentUnavailable(
            "golden non-transfer review must occur after the candidate run"
        )
    provenance = candidate.get("dataset_provenance")
    if (
        candidate.get("dataset_sha256") != manifest["dataset_sha256"]
        or not isinstance(provenance, dict)
        or provenance.get("kind") != "private-manifest-declared"
        or provenance.get("private_manifest_sha256") != private_sha
        or provenance.get("review_complete") is not True
    ):
        raise EnvironmentUnavailable(
            "real benchmark candidate/private pack binding mismatch"
        )

    exact_bindings = {
        "repository_revision": candidate.get("repository_revision"),
        "runner_bundle_sha256": _runner_bundle_sha256(command),
        "private_pack_manifest_sha256": private_sha,
        "dataset_sha256": descriptor["sha256"],
        "golden_dataset_sha256": golden_dataset_sha,
        "execution_dataset_sha256": execution_dataset_sha,
        "execution_projection_sha256": execution_projection_sha,
        "report_sha256": candidate_sha,
        "environment_hash": candidate.get("environment_hash"),
        **{name: candidate.get(name) for name in PROVIDER_MODEL_FIELDS},
    }
    if any(receipt[name] != value for name, value in exact_bindings.items()):
        raise EnvironmentUnavailable(
            "golden non-transfer receipt exact binding mismatch"
        )
    if receipt["verification_evidence_sha256"] != independent_evidence_sha:
        raise EnvironmentUnavailable(
            "golden non-transfer receipt/evidence hash mismatch"
        )
    if receipt["verified_request_count"] < manifest["records"]:
        raise EnvironmentUnavailable(
            "golden non-transfer receipt does not cover every dataset record"
        )
    if receipt["verified_request_count"] != candidate["usage"]["provider_calls"]:
        raise EnvironmentUnavailable(
            "golden non-transfer receipt request count does not match candidate usage"
        )
    if capture_evidence is not None:
        if (
            sum(capture_evidence["request_kind_counts"].values())
            != capture_evidence["request_count"]
        ):
            raise EnvironmentUnavailable(
                "independent request capture kind counts do not match total"
            )
        capture_bindings = {
            "collector_sha256": _sha(
                REPO / "scripts/capture_local_provider_boundary.py"
            ),
            "repository_revision": exact_bindings["repository_revision"],
            "runner_bundle_sha256": exact_bindings["runner_bundle_sha256"],
            "private_pack_manifest_sha256": exact_bindings[
                "private_pack_manifest_sha256"
            ],
            "dataset_sha256": exact_bindings["dataset_sha256"],
            "golden_dataset_sha256": exact_bindings["golden_dataset_sha256"],
            "execution_dataset_sha256": exact_bindings["execution_dataset_sha256"],
            "execution_projection_sha256": exact_bindings[
                "execution_projection_sha256"
            ],
            "environment_hash": exact_bindings["environment_hash"],
            **{name: exact_bindings[name] for name in PROVIDER_MODEL_FIELDS},
            "request_count": receipt["verified_request_count"],
            "ordered_request_digest": candidate.get("request_capture_ledger_sha256"),
            "session_sha256": candidate.get("request_capture_session_sha256"),
        }
        if any(
            capture_evidence[name] != value for name, value in capture_bindings.items()
        ):
            raise EnvironmentUnavailable(
                "independent request capture exact binding mismatch"
            )
        if (
            candidate.get("request_capture_request_count")
            != receipt["verified_request_count"]
        ):
            raise EnvironmentUnavailable(
                "candidate request capture count does not match receipt"
            )
        for field in ("requests_sealed_at_utc", "golden_first_open_at_utc"):
            if not capture_evidence[field].endswith(("Z", "+00:00")):
                raise EnvironmentUnavailable(
                    "independent request capture timestamps must be UTC"
                )
        requests_sealed_at = _utc(capture_evidence["requests_sealed_at_utc"])
        golden_first_open_at = _utc(capture_evidence["golden_first_open_at_utc"])
        observed_at_utc = _utc(observed_at)
        if not (
            requests_sealed_at
            < golden_first_open_at
            <= observed_at_utc
            <= verified_at_utc
        ):
            raise EnvironmentUnavailable(
                "independent request capture ordering is invalid"
            )
    unchanged_bindings = {
        "runner_bundle_sha256": _runner_bundle_sha256(command),
        "private_pack_manifest_sha256": _stable_external_sha(
            private_path, label="private pack manifest", maximum=1_000_000
        ),
        "dataset_sha256": pack_module.bundle_descriptor(golden_dataset_path.parents[1])[
            "sha256"
        ],
        "golden_dataset_sha256": _stable_external_sha(
            golden_dataset_path, label="golden dataset", maximum=8_000_000
        ),
        "execution_dataset_sha256": _stable_external_sha(
            execution_dataset_path, label="execution dataset", maximum=8_000_000
        ),
        "execution_projection_sha256": pack.execution_sha256,
        "report_sha256": _stable_external_sha(
            report_path, label="real benchmark candidate report", maximum=16_000_000
        ),
    }
    if (
        receipt_sha
        != _stable_external_sha(
            receipt_path, label="golden non-transfer receipt", maximum=1_000_000
        )
        or any(
            exact_bindings[name] != value for name, value in unchanged_bindings.items()
        )
        or independent_evidence_sha
        != _stable_external_sha(
            independent_evidence_path,
            label="independent non-transfer evidence",
            maximum=256_000_000,
        )
        or (
            capture_evidence is not None
            and capture_evidence["collector_sha256"]
            != _sha(REPO / "scripts/capture_local_provider_boundary.py")
        )
    ):
        raise EnvironmentUnavailable(
            "golden non-transfer receipt admission input changed during verification"
        )

    result = {
        "schema_version": "1.0",
        "request_type": "golden-non-transfer-receipt-check",
        "status": "RECEIPT_BOUND_TO_EXACT_CANDIDATE",
        "checker_repository_revision": _revision(),
        "source_repository_revision": candidate["repository_revision"],
        "tool_version": TOOL_VERSION,
        "receipt_sha256": receipt_sha,
        "receipt_id_hash": hashlib.sha256(receipt["receipt_id"].encode()).hexdigest(),
        "private_pack_manifest_sha256": exact_bindings["private_pack_manifest_sha256"],
        "dataset_sha256": exact_bindings["dataset_sha256"],
        "golden_dataset_sha256": exact_bindings["golden_dataset_sha256"],
        "execution_dataset_sha256": exact_bindings["execution_dataset_sha256"],
        "execution_projection_sha256": exact_bindings["execution_projection_sha256"],
        "report_sha256": exact_bindings["report_sha256"],
        "runner_bundle_sha256": exact_bindings["runner_bundle_sha256"],
        "environment_hash": exact_bindings["environment_hash"],
        **{name: exact_bindings[name] for name in PROVIDER_MODEL_FIELDS},
        "verification_method": receipt["verification_method"],
        "verification_evidence_sha256": receipt["verification_evidence_sha256"],
        "verified_request_count": receipt["verified_request_count"],
        "golden_results_sent_to_provider": False,
        "receipt_binding_verified": True,
        # The CLI can validate bytes and assertions, but cannot authenticate the
        # external reviewer or turn a receipt into release authority.
        "receipt_authority_verified": False,
        "release_gate_eligible": False,
        "human_release_decision_required": True,
        "provider_invoked": False,
    }
    if capture_evidence is not None:
        result.update(
            {
                "request_capture_session_sha256": capture_evidence["session_sha256"],
                "request_capture_ledger_sha256": capture_evidence[
                    "ordered_request_digest"
                ],
                "request_capture_request_count": capture_evidence["request_count"],
                "capture_sequence_verified": True,
            }
        )
    return result


def _validate_approval_output_path(path: Path) -> None:
    try:
        path.resolve().relative_to(REPO.resolve())
    except ValueError:
        pass
    else:
        raise EnvironmentUnavailable(
            "approval preparation output must remain outside the repository"
        )
    if path.exists() or path.is_symlink():
        raise EnvironmentUnavailable("approval preparation output must be a new file")


def _write_approval_output(path: Path, report: dict[str, Any]) -> None:
    _validate_approval_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise EnvironmentUnavailable(
            "approval preparation output must be a new file"
        ) from exc
    except OSError as exc:
        raise EnvironmentUnavailable(
            "approval preparation output could not be securely created"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise EnvironmentUnavailable(
            "approval preparation output could not be securely written"
        ) from exc


def _finite_number(
    value: Any, *, minimum: float = 0, maximum: float = math.inf
) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def _benchmark_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise EnvironmentUnavailable("provider report must be an object")
    if report.get("schema_version") != "2.0":
        raise EnvironmentUnavailable("provider report has unsupported schema version")
    if "provider" in report or "model" in report:
        raise EnvironmentUnavailable("provider report mixes legacy provenance fields")
    for name in PROVIDER_MODEL_FIELDS:
        value = report.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise EnvironmentUnavailable(
                "provider report lacks split provider/model provenance"
            )
    for name in ("embedding_profile_hash", "prompt_hash", "config_hash"):
        value = report.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise EnvironmentUnavailable("provider report lacks immutable provenance")
    capture_fields_present = {
        name for name in REQUEST_CAPTURE_REPORT_FIELDS if name in report
    }
    if capture_fields_present and capture_fields_present != set(
        REQUEST_CAPTURE_REPORT_FIELDS
    ):
        raise EnvironmentUnavailable("provider report has incomplete request capture")
    if capture_fields_present:
        for name in REQUEST_CAPTURE_REPORT_FIELDS[:2]:
            if (
                not isinstance(report[name], str)
                or re.fullmatch(r"[a-f0-9]{64}", report[name]) is None
            ):
                raise EnvironmentUnavailable(
                    "provider report has invalid request capture"
                )
        if (
            not isinstance(report["request_capture_request_count"], int)
            or isinstance(report["request_capture_request_count"], bool)
            or report["request_capture_request_count"] < 1
            or report["request_capture_request_count"] > 1_000_000
        ):
            raise EnvironmentUnavailable("provider report has invalid request capture")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise EnvironmentUnavailable("provider report lacks metrics")
    for name in ABSOLUTE_METRICS:
        if (
            not isinstance(metrics.get(name), int)
            or isinstance(metrics[name], bool)
            or metrics[name] < 0
        ):
            raise EnvironmentUnavailable("provider report has invalid safety counters")
    for name in RATE_METRICS:
        if not _finite_number(metrics.get(name), maximum=1):
            raise EnvironmentUnavailable(
                "provider report has missing/invalid quality metrics"
            )
    for name in COUNT_METRICS:
        if (
            not isinstance(metrics.get(name), int)
            or isinstance(metrics[name], bool)
            or metrics[name] < 0
        ):
            raise EnvironmentUnavailable(
                "provider report has invalid operational counters"
            )
    if not _finite_number(metrics.get("first_relevant_rank")):
        raise EnvironmentUnavailable("provider report has invalid first relevant rank")

    def percentiles(value: Any, label: str) -> dict[str, float]:
        if not isinstance(value, dict) or any(
            not _finite_number(value.get(name)) or value[name] < 0
            for name in ("p50", "p95", "p99")
        ):
            raise EnvironmentUnavailable(f"provider report has invalid {label}")
        if not value["p50"] <= value["p95"] <= value["p99"]:
            raise EnvironmentUnavailable(f"provider report has unordered {label}")
        return {name: value[name] for name in ("p50", "p95", "p99")}

    latency = metrics.get("latency_ms")
    if not isinstance(latency, dict):
        raise EnvironmentUnavailable(
            "provider report has missing/invalid latency metrics"
        )
    safe_latency = {
        name: percentiles(latency.get(name), f"{name} latency percentiles")
        for name in ("ingestion", "retrieval", "end_to_end")
    }
    safe_queue_wait = percentiles(
        metrics.get("queue_wait_ms"), "queue wait percentiles"
    )
    stage_duration = metrics.get("stage_duration_ms")
    if not isinstance(stage_duration, dict) or not stage_duration:
        raise EnvironmentUnavailable("provider report lacks stage duration metrics")
    safe_stage_duration = {}
    for stage, values in stage_duration.items():
        if not isinstance(stage, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,63}", stage
        ):
            raise EnvironmentUnavailable("provider report has invalid stage name")
        safe_stage_duration[stage] = percentiles(
            values, f"{stage} duration percentiles"
        )
    error_distribution = metrics.get("error_code_distribution")
    if not isinstance(error_distribution, dict):
        raise EnvironmentUnavailable("provider report lacks error distribution")
    safe_errors = {}
    for code, count in error_distribution.items():
        if (
            code not in ANSWER_VALIDATION_ERROR_CODES
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise EnvironmentUnavailable(
                "provider report has invalid error distribution"
            )
        safe_errors[code] = count
    breakdown = report.get("query_type_breakdown")
    if not isinstance(breakdown, dict) or not breakdown:
        raise EnvironmentUnavailable("provider report lacks query-type breakdown")
    safe_breakdown = {}
    required_breakdown = (
        "records",
        "answerability_false_positive_rate",
        "answerability_false_negative_rate",
        "citation_precision",
        "citation_coverage",
    )
    if len(breakdown) > 100:
        raise EnvironmentUnavailable(
            "provider report query-type breakdown is oversized"
        )
    for name, values in breakdown.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name)
            or not isinstance(values, dict)
        ):
            raise EnvironmentUnavailable(
                "provider report has invalid query-type breakdown"
            )
        if (
            not isinstance(values.get("records"), int)
            or isinstance(values["records"], bool)
            or values["records"] < 1
            or any(
                not _finite_number(values.get(metric), maximum=1)
                for metric in required_breakdown[1:]
            )
        ):
            raise EnvironmentUnavailable(
                "provider report has invalid query-type metrics"
            )
        safe_breakdown[name] = {metric: values[metric] for metric in required_breakdown}
    if sum(safe_errors.values()) > sum(
        values["records"] for values in safe_breakdown.values()
    ):
        raise EnvironmentUnavailable(
            "provider report error distribution exceeds dataset records"
        )
    usage = report.get("usage")
    if not isinstance(usage, dict):
        raise EnvironmentUnavailable("provider report lacks usage/cost metrics")
    for name in ("provider_calls", "input_tokens", "output_tokens"):
        if (
            not isinstance(usage.get(name), int)
            or isinstance(usage[name], bool)
            or usage[name] < 0
        ):
            raise EnvironmentUnavailable("provider report has invalid usage counters")
    if not _finite_number(usage.get("cost_usd")) or usage["cost_usd"] < 0:
        raise EnvironmentUnavailable("provider report has invalid provider cost")
    golden_transfer = report.get("golden_results_sent_to_provider")
    if golden_transfer is not None and not isinstance(golden_transfer, bool):
        raise EnvironmentUnavailable(
            "provider report has invalid golden-data assertion"
        )
    # Only the bounded contract reaches our report envelope. Arbitrary runner
    # fields must not override exact SHA/tier or copy raw prompts into evidence.
    safe_report = {
        **{
            name: report[name]
            for name in (
                "schema_version",
                *PROVIDER_MODEL_FIELDS,
                "embedding_profile_hash",
                "prompt_hash",
                "config_hash",
            )
        },
        "metrics": {
            **{
                name: metrics[name]
                for name in (*ABSOLUTE_METRICS, *RATE_METRICS, *COUNT_METRICS)
            },
            "first_relevant_rank": metrics["first_relevant_rank"],
            "latency_ms": safe_latency,
            "queue_wait_ms": safe_queue_wait,
            "stage_duration_ms": safe_stage_duration,
            "error_code_distribution": safe_errors,
        },
        "usage": {
            name: usage[name]
            for name in ("provider_calls", "input_tokens", "output_tokens", "cost_usd")
        },
        "query_type_breakdown": safe_breakdown,
        "environment_hash": report.get("environment_hash"),
        "golden_results_sent_to_provider": golden_transfer,
    }
    if capture_fields_present:
        safe_report.update(
            {name: report[name] for name in REQUEST_CAPTURE_REPORT_FIELDS}
        )
    return safe_report


def _real_benchmark(args: argparse.Namespace, work: Path) -> dict[str, Any]:
    if (
        not args.approval_manifest
        or not args.private_pack_manifest
        or not args.provider_runner
    ):
        raise EnvironmentUnavailable(
            "real-benchmark requires approval manifest, private manifest and provider runner"
        )
    manifest, approval, private_sha, approval_sha = _bound_approval(
        args.approval_manifest, args.private_pack_manifest, args.provider_runner
    )
    capture_socket = getattr(args, "capture_socket", None)
    capture_nonce_file = getattr(args, "capture_nonce_file", None)
    if manifest["schema_version"] == "3.0":
        if capture_socket is None or capture_nonce_file is None:
            raise EnvironmentUnavailable(
                "v3 real-benchmark requires capture socket and nonce file"
            )
        capture_socket_value = _capture_socket_value(capture_socket)
        capture_nonce_value = _capture_nonce_value(capture_nonce_file)
        repository_revision = _revision()
    else:
        if capture_socket is not None or capture_nonce_file is not None:
            raise EnvironmentUnavailable(
                "request capture inputs require a v3 private manifest"
            )
        capture_socket_value = None
        capture_nonce_value = None
    environment_hash = _environment_sha256()
    if environment_hash != approval["environment_hash"]:
        raise EnvironmentUnavailable(
            "benchmark execution environment changed before runner execution"
        )
    output = work / "provider-report.json"
    credential_env = {}
    for name in approval["credential_env_names"]:
        if name not in os.environ:
            raise EnvironmentUnavailable("approved provider credential is unavailable")
        credential_env[name] = os.environ[name]
    env = {
        **credential_env,
        "PATH": _runner_path(),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": str(work / "isolated-pycache"),
        "CV_EVAL_OUTPUT": str(output),
    }
    env.update(
        {
            "CV_EVAL_APPROVAL_ID": approval["approval_id"],
            "CV_EVAL_MAX_PROVIDER_CALLS": str(approval["max_provider_calls"]),
            "CV_EVAL_MAX_INPUT_TOKENS": str(approval["max_input_tokens"]),
            "CV_EVAL_MAX_OUTPUT_TOKENS": str(approval["max_output_tokens"]),
            "CV_EVAL_MAX_COST_USD": str(approval["max_cost_usd"]),
            "CV_EVAL_MAX_DURATION_SECONDS": str(approval["max_duration_seconds"]),
            "CV_EVAL_EMBEDDING_PROVIDER": approval["embedding_provider"],
            "CV_EVAL_EMBEDDING_MODEL": approval["embedding_model"],
            "CV_EVAL_GENERATION_PROVIDER": approval["generation_provider"],
            "CV_EVAL_GENERATION_MODEL": approval["generation_model"],
            "CV_EVAL_ENVIRONMENT_HASH": environment_hash,
            "CV_EVAL_CURRENCY": approval["currency"],
            "CV_EVAL_DATASET_SHA256": manifest["dataset_sha256"],
            "CV_EVAL_PRIVATE_PACK_MANIFEST_SHA256": private_sha,
            "CV_EVAL_PRIVATE_PACK_CLASSIFICATION": manifest["classification"],
            "CV_EVAL_PRIVATE_PACK_RECORDS": str(manifest["records"]),
            "CV_EVAL_PRIVATE_PACK_QUERY_TYPES": json.dumps(
                manifest["query_types"], separators=(",", ":")
            ),
            "CV_EVAL_RUNNER_BUNDLE_SHA256": _runner_bundle_sha256(args.provider_runner),
        }
    )
    if manifest["schema_version"] == "3.0":
        env.update(
            {
                "CV_EVAL_GOLDEN_DATASET_SHA256": manifest["golden_dataset_sha256"],
                "CV_EVAL_EXECUTION_DATASET_SHA256": manifest[
                    "execution_dataset_sha256"
                ],
                "CV_EVAL_EXECUTION_PROJECTION_SHA256": manifest[
                    "execution_projection_sha256"
                ],
                "CV_EVAL_CAPTURE_SOCKET": capture_socket_value,
                "CV_EVAL_CAPTURE_NONCE": capture_nonce_value,
                "CV_EVAL_REPOSITORY_REVISION": repository_revision,
            }
        )
    if (
        env["CV_EVAL_RUNNER_BUNDLE_SHA256"] != approval["runner_bundle_sha256"]
        or _environment_sha256() != environment_hash
    ):
        raise EnvironmentUnavailable(
            "benchmark admission input changed before runner execution"
        )
    _assert_approval_inputs_unchanged(
        approval_path=args.approval_manifest,
        approval_sha=approval_sha,
        private_path=args.private_pack_manifest,
        private_sha=private_sha,
    )
    try:
        completed = subprocess.run(
            args.provider_runner,
            cwd=REPO,
            env=env,
            capture_output=True,
            timeout=approval["max_duration_seconds"],
        )
    except subprocess.TimeoutExpired as exc:
        raise EnvironmentUnavailable(
            "approved provider runner exceeded duration limit"
        ) from exc
    try:
        manifest_unchanged = (
            _stable_external_sha(
                args.private_pack_manifest,
                label="private pack manifest",
                maximum=1_000_000,
            )
            == private_sha
        )
        approval_unchanged = (
            _stable_external_sha(
                args.approval_manifest,
                label="benchmark approval manifest",
                maximum=1_000_000,
            )
            == approval_sha
        )
        runner_unchanged = (
            _runner_bundle_sha256(args.provider_runner)
            == approval["runner_bundle_sha256"]
        )
        environment_unchanged = _environment_sha256() == environment_hash
        revision_unchanged = (
            manifest["schema_version"] != "3.0" or _revision() == repository_revision
        )
    except (OSError, EnvironmentUnavailable) as exc:
        raise EnvironmentUnavailable(
            "benchmark admission input changed during runner execution"
        ) from exc
    if (
        not manifest_unchanged
        or not approval_unchanged
        or not runner_unchanged
        or not environment_unchanged
        or not revision_unchanged
    ):
        raise EnvironmentUnavailable(
            "benchmark admission input changed during runner execution"
        )
    if completed.returncode != 0 or not output.exists():
        raise EnvironmentUnavailable("approved provider runner failed")
    report = _benchmark_report(json.loads(output.read_text()))
    if manifest["schema_version"] == "3.0":
        if any(name not in report for name in REQUEST_CAPTURE_REPORT_FIELDS):
            raise EnvironmentUnavailable(
                "v3 provider report lacks sealed request capture"
            )
        if report["request_capture_request_count"] != report["usage"]["provider_calls"]:
            raise EnvironmentUnavailable(
                "provider report request capture count does not match usage"
            )
    if (
        set(report["query_type_breakdown"]) != set(manifest["query_types"])
        or sum(item["records"] for item in report["query_type_breakdown"].values())
        != manifest["records"]
    ):
        raise EnvironmentUnavailable(
            "provider report query-type breakdown does not match private dataset"
        )
    if any(report[name] != approval[name] for name in PROVIDER_MODEL_FIELDS):
        raise EnvironmentUnavailable("provider report does not match approval")
    if report["environment_hash"] != approval["environment_hash"]:
        raise EnvironmentUnavailable(
            "provider report environment does not match approval"
        )
    usage = report["usage"]
    for used, allowed in (
        (usage["provider_calls"], approval["max_provider_calls"]),
        (usage["input_tokens"], approval["max_input_tokens"]),
        (usage["output_tokens"], approval["max_output_tokens"]),
        (usage["cost_usd"], approval["max_cost_usd"]),
    ):
        if used > allowed:
            raise EnvironmentUnavailable(
                "provider report exceeds approved usage budget"
            )
    metrics = report["metrics"]
    absolute_pass = (
        all(metrics.get(name) == 0 for name in ABSOLUTE_METRICS)
        and all(metrics.get(name) == 0 for name in ZERO_RATE_METRICS)
        and report["golden_results_sent_to_provider"] is False
    )
    return {
        **report,
        "result": "PASS" if absolute_pass else "FAIL",
        "approval_id_hash": hashlib.sha256(
            approval["approval_id"].encode()
        ).hexdigest(),
        "approval_manifest_sha256": approval_sha,
        "dataset_sha256": manifest["dataset_sha256"],
        "dataset_provenance": {
            "kind": "private-manifest-declared",
            "private_manifest_sha256": private_sha,
            "review_complete": True,
        },
        "private_pack": {
            "opaque_pack_id": manifest["opaque_pack_id"],
            "dataset_sha256": manifest["dataset_sha256"],
            "classification": manifest["classification"],
        },
        # Shape and reported counters cannot prove actual provider execution,
        # non-leakage of golden answers or human approval of a baseline.
        "release_gate_eligible": False,
        "baseline_review_required": True,
        "quality_claim": "runner-reported-unverified",
        "evidence_basis": "provider-runner report; not independently observed",
        "warnings": (
            []
            if report["golden_results_sent_to_provider"] is False
            else ["golden-data non-transfer requires independent verification"]
        ),
    }


def _compare_reports(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    current = report.get("metrics", {})
    previous = baseline.get("metrics", {})
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return ["baseline/current metrics must be objects"]
    findings = []
    for name in QUALITY_METRICS:
        if not _finite_number(current.get(name), maximum=1) or not _finite_number(
            previous.get(name), maximum=1
        ):
            findings.append(f"{name} missing or invalid for regression comparison")
        elif current[name] < previous[name] - 0.02 - 1e-12:
            findings.append(f"{name} regressed by more than 0.02")
    current_latency = (
        current.get("latency_ms", {}).get("end_to_end")
        if isinstance(current.get("latency_ms"), dict)
        else None
    )
    previous_latency = (
        previous.get("latency_ms", {}).get("end_to_end")
        if isinstance(previous.get("latency_ms"), dict)
        else None
    )
    current_p95 = (
        current_latency.get("p95") if isinstance(current_latency, dict) else None
    )
    baseline_p95 = (
        previous_latency.get("p95") if isinstance(previous_latency, dict) else None
    )
    if (
        not _finite_number(current_p95)
        or not _finite_number(baseline_p95)
        or current_p95 <= 0
        or baseline_p95 <= 0
    ):
        findings.append("p95 latency missing or invalid for regression comparison")
    elif current_p95 > baseline_p95 * 1.2:
        findings.append("p95 latency regressed by more than 20 percent")
    return findings


def _compare(report: dict[str, Any], baseline_path: Path | None) -> list[str]:
    if baseline_path is None:
        return []
    baseline = json.loads(baseline_path.read_text())
    if not isinstance(baseline, dict):
        return ["baseline/current metrics must be objects"]
    return _compare_reports(report, baseline)


def _load_sealed_baseline(baseline_path: Path, seal_path: Path) -> dict[str, Any]:
    seal_payload, seal_sha = _read_stable_external(
        seal_path, label="baseline seal", maximum=1_000_000
    )
    baseline_payload, baseline_sha = _read_stable_external(
        baseline_path, label="real benchmark baseline", maximum=16_000_000
    )
    seal = _schema_payload(seal_payload, BASELINE_SEAL_SCHEMA, "baseline seal")
    if _utc(seal["sealed_at_utc"]) > datetime.now(timezone.utc):
        raise EnvironmentUnavailable("baseline seal timestamp is in the future")
    if seal["report_sha256"] != baseline_sha:
        raise EnvironmentUnavailable("baseline seal/report hash mismatch")
    baseline = json.loads(baseline_payload)
    if (
        not isinstance(baseline, dict)
        or baseline.get("schema_version") != "2.0"
        or baseline.get("tier") != "real-benchmark"
        or baseline.get("result") != "PASS"
    ):
        raise EnvironmentUnavailable("baseline is not a successful real benchmark")
    _benchmark_report(baseline)
    provenance = (
        *PROVIDER_MODEL_FIELDS,
        "dataset_sha256",
        "embedding_profile_hash",
        "prompt_hash",
        "config_hash",
        "environment_hash",
    )
    if any(baseline.get(name) != seal[name] for name in provenance):
        raise EnvironmentUnavailable("baseline seal provenance mismatch")
    return {
        "baseline": baseline,
        "seal": seal,
        "baseline_report_sha256": baseline_sha,
        "baseline_seal_sha256": seal_sha,
    }


def _prevalidate_baseline_inputs(
    baseline_path: Path | None, seal_path: Path | None
) -> dict[str, Any] | None:
    if baseline_path is None and seal_path is None:
        return None
    if baseline_path is None:
        raise EnvironmentUnavailable("baseline seal provided without baseline")
    if seal_path is None:
        raise EnvironmentUnavailable(
            "real benchmark baseline requires an approved seal"
        )
    return _load_sealed_baseline(baseline_path, seal_path)


def _compare_bound_baseline(
    report: dict[str, Any],
    binding: dict[str, Any],
) -> list[str]:
    baseline = binding["baseline"]
    _benchmark_report(report)
    provenance = (
        *PROVIDER_MODEL_FIELDS,
        "dataset_sha256",
        "embedding_profile_hash",
        "prompt_hash",
        "config_hash",
        "environment_hash",
    )
    findings = []
    for name in provenance:
        if report.get(name) != baseline.get(name):
            findings.append(f"{name} differs from approved baseline")
    return findings + _compare_reports(report, baseline)


def _compare_with_seal(
    report: dict[str, Any], baseline_path: Path | None, seal_path: Path | None
) -> list[str]:
    if baseline_path is None:
        return ["baseline seal provided without baseline"] if seal_path else []
    if seal_path is None:
        return ["real benchmark baseline requires an approved seal"]
    return _compare_bound_baseline(
        report, _load_sealed_baseline(baseline_path, seal_path)
    )


def _apply_strict(report: dict[str, Any], strict: bool) -> None:
    if report.get("regression_findings") or (strict and report.get("warnings")):
        report["result"] = "FAIL"


def _finalize_real_gate(report: dict[str, Any], has_approved_baseline: bool) -> None:
    if report.get("tier") != "real-benchmark":
        return
    if not has_approved_baseline:
        report.setdefault("warnings", []).append(
            "first real-provider baseline requires independent human seal"
        )
    report["baseline_review_required"] = not has_approved_baseline
    report["regression_candidate_eligible"] = bool(
        has_approved_baseline
        and report.get("result") == "PASS"
        and not report.get("warnings")
        and not report.get("regression_findings")
    )
    # Current-run independent review cannot happen inside the runner that made
    # the report. A later human/independent verifier may consume the candidate.
    report["release_gate_eligible"] = False
    if report["regression_candidate_eligible"]:
        report["quality_claim"] = "approved-baseline-regression-candidate"


def _markdown(report: dict[str, Any]) -> str:
    provenance = report.get("dataset_provenance")
    provenance_kind = (
        provenance.get("kind") if isinstance(provenance, dict) else "unspecified"
    )
    dataset_sha = report.get("dataset_sha256") or "not-applicable"
    return "\n".join(
        [
            f"# {report['tier']} evaluation",
            "",
            f"- result: `{report['result']}`",
            f"- revision: `{report['repository_revision']}`",
            f"- dataset sha256: `{dataset_sha}`",
            f"- dataset provenance: `{provenance_kind}`",
            f"- release gate eligible: `{str(report['release_gate_eligible']).lower()}`",
            f"- regression findings: `{len(report['regression_findings'])}`",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--tier",
        choices=("contract-smoke", "offline-e2e", "real-benchmark"),
    )
    mode.add_argument("--approval-preflight", action="store_true")
    mode.add_argument("--check-approval", action="store_true")
    mode.add_argument("--check-golden-non-transfer-receipt", action="store_true")
    mode.add_argument("--public-dataset-status", action="store_true")
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--approval-manifest", type=Path)
    parser.add_argument("--baseline-seal", type=Path)
    parser.add_argument("--private-pack-manifest", type=Path)
    parser.add_argument("--public-review-receipt", type=Path)
    parser.add_argument("--golden-non-transfer-receipt", type=Path)
    parser.add_argument("--candidate-report", type=Path)
    parser.add_argument("--golden-dataset", type=Path)
    parser.add_argument("--execution-dataset", type=Path)
    parser.add_argument("--non-transfer-evidence", type=Path)
    parser.add_argument("--capture-socket", type=Path)
    parser.add_argument("--capture-nonce-file", type=Path)
    parser.add_argument("--provider-runner", nargs="+")
    args = parser.parse_args()
    try:
        if args.public_dataset_status:
            if any(
                (
                    args.markdown_output,
                    args.baseline,
                    args.strict,
                    args.approval_manifest,
                    args.baseline_seal,
                    args.private_pack_manifest,
                    args.provider_runner,
                    args.golden_non_transfer_receipt,
                    args.candidate_report,
                    args.golden_dataset,
                    args.execution_dataset,
                    args.non_transfer_evidence,
                    args.capture_socket,
                    args.capture_nonce_file,
                )
            ):
                raise EnvironmentUnavailable(
                    "public dataset status accepts only its JSON output and optional review receipt"
                )
            report = _public_dataset_status(
                PUBLIC_DATASET,
                PUBLIC_DATASET_MANIFEST,
                args.public_review_receipt,
            )
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n"
            )
            print(
                json.dumps(
                    {
                        "request_type": report["request_type"],
                        "integrity": report["integrity"],
                        "review_status": report["review_status"],
                        "receipt_binding_verified": report["receipt_binding_verified"],
                        "provider_invoked": False,
                    }
                )
            )
            return 0
        if args.public_review_receipt:
            raise EnvironmentUnavailable(
                "public review receipt requires public dataset status"
            )
        receipt_check_inputs = (
            args.golden_non_transfer_receipt,
            args.candidate_report,
            args.golden_dataset,
            args.execution_dataset,
            args.non_transfer_evidence,
        )
        if args.check_golden_non_transfer_receipt:
            if (
                not all(receipt_check_inputs)
                or not args.private_pack_manifest
                or not args.provider_runner
            ):
                raise EnvironmentUnavailable(
                    "golden non-transfer receipt check requires receipt, candidate report, private manifest, golden dataset, execution dataset, independent evidence and provider runner"
                )
            if any(
                (
                    args.markdown_output,
                    args.baseline,
                    args.baseline_seal,
                    args.strict,
                    args.approval_manifest,
                    args.capture_socket,
                    args.capture_nonce_file,
                )
            ):
                raise EnvironmentUnavailable(
                    "golden non-transfer receipt check accepts only its exact binding inputs and JSON output"
                )
            _validate_approval_output_path(args.json_output)
            report = _check_golden_non_transfer_receipt(
                args.golden_non_transfer_receipt,
                args.candidate_report,
                args.private_pack_manifest,
                args.golden_dataset,
                args.execution_dataset,
                args.non_transfer_evidence,
                args.provider_runner,
            )
            _write_approval_output(args.json_output, report)
            print(
                json.dumps(
                    {
                        "request_type": report["request_type"],
                        "status": report["status"],
                        "receipt_binding_verified": True,
                        "receipt_authority_verified": False,
                        "release_gate_eligible": False,
                        "provider_invoked": False,
                    }
                )
            )
            return 0
        if any(receipt_check_inputs):
            raise EnvironmentUnavailable(
                "golden non-transfer receipt inputs require receipt-check mode"
            )
        if args.approval_preflight or args.check_approval:
            if not args.private_pack_manifest or not args.provider_runner:
                raise EnvironmentUnavailable(
                    "approval preparation requires private manifest and provider runner"
                )
            if (
                args.baseline
                or args.baseline_seal
                or args.strict
                or args.capture_socket
                or args.capture_nonce_file
            ):
                raise EnvironmentUnavailable(
                    "approval preparation does not accept eval, baseline or capture inputs"
                )
            if args.markdown_output:
                raise EnvironmentUnavailable(
                    "approval preparation emits only bounded JSON"
                )
            _validate_approval_output_path(args.json_output)
            if args.approval_preflight:
                if args.approval_manifest:
                    raise EnvironmentUnavailable(
                        "approval preflight cannot consume or create an approval"
                    )
                report = _approval_preflight(
                    args.private_pack_manifest, args.provider_runner
                )
            else:
                if not args.approval_manifest:
                    raise EnvironmentUnavailable(
                        "approval check requires a human approval manifest"
                    )
                report = _check_approval(
                    args.approval_manifest,
                    args.private_pack_manifest,
                    args.provider_runner,
                )
            _write_approval_output(args.json_output, report)
            print(
                json.dumps(
                    {
                        "request_type": report["request_type"],
                        "status": report["status"],
                        "provider_invoked": False,
                    }
                )
            )
            return 0
        if args.markdown_output is None:
            raise EnvironmentUnavailable("eval tier requires markdown output")
        if args.tier != "real-benchmark" and (
            args.capture_socket or args.capture_nonce_file
        ):
            raise EnvironmentUnavailable(
                "request capture inputs are supported only for real-benchmark"
            )
        sealed_baseline = None
        if args.tier == "real-benchmark":
            sealed_baseline = _prevalidate_baseline_inputs(
                args.baseline, args.baseline_seal
            )
        elif args.baseline is not None or args.baseline_seal is not None:
            raise EnvironmentUnavailable(
                "baseline inputs are supported only for the real benchmark tier"
            )
        with tempfile.TemporaryDirectory(prefix="cv-eval-") as directory:
            work = Path(directory)
            if args.tier == "contract-smoke":
                details = _contract_smoke(work)
            elif args.tier == "offline-e2e":
                details = _offline_e2e(work)
            else:
                details = _real_benchmark(args, work)
        report = {
            **details,
            "schema_version": ("2.0" if args.tier == "real-benchmark" else "1.0"),
            "tool_version": TOOL_VERSION,
            "tier": args.tier,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "repository_revision": _revision(),
            "dataset_sha256": details.get("dataset_sha256"),
            "dataset_provenance": details.get("dataset_provenance"),
        }
        report["regression_findings"] = (
            _compare_bound_baseline(report, sealed_baseline)
            if sealed_baseline is not None
            else []
        )
        if sealed_baseline is not None:
            report["baseline_report_sha256"] = sealed_baseline["baseline_report_sha256"]
            report["baseline_seal_sha256"] = sealed_baseline["baseline_seal_sha256"]
        _finalize_real_gate(
            report,
            sealed_baseline is not None and not report["regression_findings"],
        )
        _apply_strict(report, args.strict)
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        args.markdown_output.write_text(_markdown(report))
        print(json.dumps({"tier": args.tier, "result": report["result"]}))
        return 0 if report["result"] == "PASS" else 1
    except (OSError, ValueError, ImportError, EnvironmentUnavailable) as exc:
        mode_name = (
            "public-dataset-review-status"
            if args.public_dataset_status
            else "real-benchmark-approval-preflight"
            if args.approval_preflight
            else "real-benchmark-approval-check"
            if args.check_approval
            else "golden-non-transfer-receipt-check"
            if args.check_golden_non_transfer_receipt
            else None
        )
        failure: dict[str, Any] = {
            "result": "ENVIRONMENT_UNAVAILABLE",
            "reason": str(exc)
            if isinstance(exc, EnvironmentUnavailable)
            else "eval input/output or tooling unavailable",
        }
        if mode_name:
            failure["request_type"] = mode_name
            failure["provider_invoked"] = False
        else:
            failure["tier"] = args.tier
        print(json.dumps(failure))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
