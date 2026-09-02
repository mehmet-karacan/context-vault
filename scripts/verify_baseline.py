#!/usr/bin/env python3
"""Verify the Context Vault repository baseline without importing application code."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TOOL_VERSION = "1.1.0"
DEFAULT_EXPECTED_SHA = "6b99a53d19e7f5f7b07b403e751c629f79ab7663"
DEFAULT_AUDIT_RELATIVE = Path(
    "document-rag-platform/artifacts/audit/2026-09-02-baseline"
)
VERSION_CANDIDATES = (
    Path("document-rag-platform/services/backend/alembic/versions_v3"),
    Path("document-rag-platform/services/backend/alembic/versions"),
)


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
        }
        if self.path is not None:
            result["path"] = self.path
        return result


class EnvironmentUnavailable(RuntimeError):
    """Raised when a required read-only dependency cannot be inspected."""


def run_git(repo: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise EnvironmentUnavailable(
            f"git command failed: {' '.join(arguments)}"
        ) from exc
    return completed.stdout.rstrip("\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            target_name = target.id if isinstance(target, ast.Name) else None
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target_name = node.target.id if isinstance(node.target, ast.Name) else None
            value = node.value
        if target_name == name and value is not None:
            return ast.literal_eval(value)
    raise ValueError(f"missing literal assignment: {name}")


def migration_inventory(repo: Path) -> tuple[list[dict[str, Any]], list[str]]:
    versions_relative = next(
        (
            candidate
            for candidate in VERSION_CANDIDATES
            if (repo / candidate).is_dir() and any((repo / candidate).glob("*.py"))
        ),
        None,
    )
    if versions_relative is None:
        raise EnvironmentUnavailable(
            "migration directory unavailable: "
            + ", ".join(str(candidate) for candidate in VERSION_CANDIDATES)
        )
    versions_dir = repo / versions_relative

    revisions: list[dict[str, Any]] = []
    referenced: set[str] = set()
    for path in sorted(versions_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            revision = literal_assignment(tree, "revision")
            down_revision = literal_assignment(tree, "down_revision")
        except (OSError, SyntaxError, ValueError) as exc:
            raise EnvironmentUnavailable(
                f"cannot parse migration: {path.name}"
            ) from exc
        if not isinstance(revision, str):
            raise EnvironmentUnavailable(f"invalid revision in migration: {path.name}")
        parents: list[str]
        if down_revision is None:
            parents = []
        elif isinstance(down_revision, str):
            parents = [down_revision]
        elif isinstance(down_revision, (tuple, list)) and all(
            isinstance(item, str) for item in down_revision
        ):
            parents = list(down_revision)
        else:
            raise EnvironmentUnavailable(
                f"invalid down_revision in migration: {path.name}"
            )
        referenced.update(parents)
        revisions.append(
            {
                "path": path.relative_to(repo).as_posix(),
                "revision": revision,
                "down_revision": parents,
                "sha256": sha256_file(path),
            }
        )
    heads = sorted(
        item["revision"] for item in revisions if item["revision"] not in referenced
    )
    return revisions, heads


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "credential-in-url",
        re.compile(r"\b(?:postgres(?:ql)?|redis|https?)://[^\s/:]+:[^\s/@]+@"),
    ),
    (
        "assigned-secret",
        re.compile(
            r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|secret)\b"
            r"\s*[:=]\s*[\"'][^\"'\s{}$]{8,}[\"']"
        ),
    ),
)


def iter_audit_files(audit_dir: Path) -> Iterable[Path]:
    for path in sorted(audit_dir.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            yield path


def scan_audit_directory(audit_dir: Path) -> tuple[list[dict[str, str]], list[Finding]]:
    if not audit_dir.is_dir():
        return [], [
            Finding(
                "audit-directory",
                "warning",
                "public audit directory is not present",
                audit_dir.name,
            )
        ]

    inventory: list[dict[str, str]] = []
    findings: list[Finding] = []
    for path in iter_audit_files(audit_dir):
        relative = path.relative_to(audit_dir).as_posix()
        inventory.append({"path": relative, "sha256": sha256_file(path)})
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(
                Finding(
                    "audit-public-safety",
                    "error",
                    "binary content is not allowed in the public audit tree",
                    relative,
                )
            )
            continue
        for rule_name, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(
                    Finding(
                        "audit-public-safety",
                        "error",
                        f"potential secret matched rule {rule_name}",
                        relative,
                    )
                )
    return inventory, findings


def verify_audit_hashes(
    repo: Path, audit_dir: Path
) -> tuple[dict[str, Any], list[Finding]]:
    findings: list[Finding] = []
    ledger_path = audit_dir / "SHA256SUMS"
    ledger_results: list[dict[str, Any]] = []
    if not ledger_path.is_file():
        return {"ledger": "MISSING", "files": []}, [
            Finding("audit-checksums", "error", "SHA256SUMS is missing")
        ]

    for line_number, line in enumerate(
        ledger_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            findings.append(
                Finding(
                    "audit-checksums",
                    "error",
                    f"invalid checksum ledger line {line_number}",
                    "SHA256SUMS",
                )
            )
            continue
        expected, relative = match.groups()
        target = audit_dir / relative
        actual = sha256_file(target) if target.is_file() else None
        valid = actual == expected
        ledger_results.append({"path": relative, "valid": valid})
        if not valid:
            findings.append(
                Finding(
                    "audit-checksums",
                    "error",
                    "checksum mismatch or missing artifact",
                    relative,
                )
            )

    manifest_path = audit_dir / "audit-manifest.json"
    manifest_results: list[dict[str, Any]] = []
    if not manifest_path.is_file():
        findings.append(
            Finding("audit-manifest-hashes", "error", "audit manifest is missing")
        )
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact_hashes = manifest["artifact_hashes"]
        except (KeyError, json.JSONDecodeError, OSError) as exc:
            raise EnvironmentUnavailable("cannot parse audit manifest hashes") from exc
        for relative, expected in sorted(artifact_hashes.items()):
            target = (
                repo / relative
                if relative.startswith("scripts/")
                else audit_dir / relative
            )
            actual = sha256_file(target) if target.is_file() else None
            valid = actual == expected
            manifest_results.append({"path": relative, "valid": valid})
            if not valid:
                findings.append(
                    Finding(
                        "audit-manifest-hashes",
                        "error",
                        "manifest hash mismatch or missing artifact",
                        relative,
                    )
                )

    return {
        "ledger": "PASS"
        if not any(not item["valid"] for item in ledger_results)
        else "FAIL",
        "files": ledger_results,
        "manifest_artifacts": manifest_results,
    }, findings


def sensitive_path_candidates(repo: Path) -> list[str]:
    tracked = run_git(repo, "ls-files").splitlines()
    suffixes = {".crt", ".cer", ".pem", ".key", ".p12", ".pfx", ".dump", ".sql"}
    candidates: list[str] = []
    for relative in tracked:
        path = Path(relative)
        name = path.name.lower()
        if (
            path.suffix.lower() in suffixes
            or name == ".env"
            or name.startswith(".env.")
        ):
            candidates.append(relative)
    return sorted(candidates)


def public_relative_path(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return "<external-audit-directory>"


def verify(
    repo: Path,
    expected_sha: str,
    audit_dir: Path,
    strict: bool,
) -> tuple[dict[str, Any], int]:
    repo = repo.resolve()
    head = run_git(repo, "rev-parse", "HEAD")
    branch = run_git(repo, "branch", "--show-current") or "DETACHED"
    status_lines = run_git(repo, "status", "--short").splitlines()
    revisions, heads = migration_inventory(repo)
    audit_inventory, findings = scan_audit_directory(audit_dir.resolve())
    audit_hash_verification, hash_findings = verify_audit_hashes(
        repo, audit_dir.resolve()
    )
    findings.extend(hash_findings)

    if head != expected_sha:
        findings.append(
            Finding("head-sha", "error", "HEAD does not match expected SHA")
        )
    if status_lines:
        findings.append(Finding("working-tree", "error", "working tree is not clean"))
    if len(heads) != 1:
        findings.append(
            Finding("migration-heads", "error", "migration graph is not single-head")
        )

    failing_severities = {"error"}
    if strict:
        failing_severities.add("warning")
    failed = any(item.severity in failing_severities for item in findings)

    report: dict[str, Any] = {
        "schema": "context-vault-baseline-verification/v1",
        "tool_version": TOOL_VERSION,
        "observed_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "repository": {
            "root": ".",
            "branch": branch,
            "head_sha": head,
            "expected_sha": expected_sha,
            "dirty": bool(status_lines),
            "dirty_paths": status_lines,
        },
        "migrations": {
            "heads": heads,
            "revisions": revisions,
        },
        "public_audit": {
            "root": public_relative_path(audit_dir, repo),
            "files": audit_inventory,
            "hash_verification": audit_hash_verification,
        },
        "sensitive_path_candidates": sensitive_path_candidates(repo),
        "findings": [item.as_dict() for item in findings],
        "result": "FAIL" if failed else "PASS",
    }
    return report, 1 if failed else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--expected-sha", default=DEFAULT_EXPECTED_SHA)
    parser.add_argument("--audit-dir", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    audit_dir = args.audit_dir or args.repo / DEFAULT_AUDIT_RELATIVE
    try:
        report, exit_code = verify(args.repo, args.expected_sha, audit_dir, args.strict)
    except EnvironmentUnavailable as exc:
        report = {
            "schema": "context-vault-baseline-verification/v1",
            "tool_version": TOOL_VERSION,
            "observed_at_utc": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "result": "ENVIRONMENT_UNAVAILABLE",
            "error": str(exc),
        }
        exit_code = 3

    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
