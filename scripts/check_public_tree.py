#!/usr/bin/env python3
"""Fail closed when the active public source tree contains private deployment data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


# Split scanner signatures so the scanner source does not exempt itself from the
# same checks it applies to every other tracked file.
FORBIDDEN_ENDPOINTS = ("turktelekom" + ".com.tr",)
PRIVATE_KEY = re.compile(
    re.escape("-----BEGIN ") + r"(?:RSA |EC |OPENSSH )?" + re.escape("PRIVATE KEY-----")
)
RUNTIME_SUFFIXES = {
    ".bak",
    ".db",
    ".dump",
    ".gz",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
    ".zst",
    ".bz2",
    ".7z",
}
CERTIFICATE_SUFFIXES = {".cer", ".crt"}
EXACT_SYNTHETIC_FIXTURES = {
    (
        "document-rag-platform/services/backend/tests/test_redaction.py",
        "390b2c4efe2e5dd00b95fb1eeb72c44350d27286a1f2a55446c91d23b81a0451",
    ),
    (
        "scripts/verify_baseline.py",
        "dc3f0f742da568f48d8858bdad1691c2b8a4c62b698fea778cc2134be1cace50",
    ),
}


def tracked_files(repo: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return sorted(repo / item.decode() for item in result.stdout.split(b"\0") if item)


def scan_repository(repo: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    files = tracked_files(repo)
    checked = 0
    for path in files:
        # ``git ls-files`` includes paths deleted in an uncommitted candidate;
        # those files are no longer part of the active working tree.
        if not path.exists() and not path.is_symlink():
            continue
        checked += 1
        relative = path.relative_to(repo).as_posix()
        suffix = path.suffix.lower()
        if suffix in RUNTIME_SUFFIXES or suffix in CERTIFICATE_SUFFIXES:
            findings.append({"rule": "private-or-runtime-artifact", "path": relative})
            continue
        if path.is_symlink():
            content = os.readlink(path)
            raw_content = content.encode("utf-8", errors="surrogateescape")
        else:
            raw_content = path.read_bytes()
            # Replacement decoding keeps ASCII signatures searchable even when
            # an otherwise-text file contains malformed UTF-8 bytes.
            content = raw_content.decode("utf-8", errors="replace")
        content_hash = hashlib.sha256(raw_content).hexdigest()
        lowered = content.lower()
        if any(endpoint in lowered for endpoint in FORBIDDEN_ENDPOINTS):
            findings.append({"rule": "private-endpoint", "path": relative})
        if (
            PRIVATE_KEY.search(content)
            and (relative, content_hash) not in EXACT_SYNTHETIC_FIXTURES
        ):
            findings.append({"rule": "private-key", "path": relative})

    return {
        "schema_version": 1,
        "status": "PASS" if not findings else "FAIL",
        "scope": ["all-git-tracked-files"],
        "tracked_files_total": len(files),
        "tracked_files_checked": checked,
        "findings": findings,
    }


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError(f"refusing unsafe receipt path: {path}") from exc
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()

    receipt = scan_repository(repo)
    write_receipt(args.json_output, receipt)
    print(
        "public tree: "
        f"{receipt['status']} ({receipt['tracked_files_checked']} tracked files checked)"
    )
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
