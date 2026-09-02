#!/usr/bin/env python3
"""Fail closed when the active public source tree contains private deployment data."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


FORBIDDEN_ENDPOINTS = ("turktelekom.com.tr",)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
RUNTIME_SUFFIXES = {".dump", ".sqlite", ".sqlite3", ".db", ".pem", ".key", ".p12"}
SOURCE_ROOTS = (
    ".github",
    "document-rag-platform/apps",
    "document-rag-platform/services",
)
EXACT_SYNTHETIC_FIXTURES = {
    (
        "document-rag-platform/services/backend/tests/test_redaction.py",
        "390b2c4efe2e5dd00b95fb1eeb72c44350d27286a1f2a55446c91d23b81a0451",
    )
}


def tracked_files(repo: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--", *SOURCE_ROOTS],
        check=True,
        capture_output=True,
    )
    return [repo / item.decode() for item in result.stdout.split(b"\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()

    findings: list[dict[str, str]] = []
    files = tracked_files(repo)
    for path in files:
        # ``git ls-files`` includes paths deleted in an uncommitted candidate;
        # those files are no longer part of the active working tree.
        if not path.exists():
            continue
        relative = path.relative_to(repo).as_posix()
        if path.suffix.lower() in RUNTIME_SUFFIXES or relative.endswith(".crt"):
            findings.append({"rule": "private-or-runtime-artifact", "path": relative})
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        lowered = content.lower()
        if any(endpoint in lowered for endpoint in FORBIDDEN_ENDPOINTS):
            findings.append({"rule": "private-endpoint", "path": relative})
        if (
            PRIVATE_KEY.search(content)
            and (relative, content_hash) not in EXACT_SYNTHETIC_FIXTURES
        ):
            findings.append({"rule": "private-key", "path": relative})

    receipt = {
        "schema_version": 1,
        "status": "PASS" if not findings else "FAIL",
        "scope": list(SOURCE_ROOTS),
        "tracked_files_checked": len(files),
        "findings": findings,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"public tree: {receipt['status']} ({len(files)} tracked files checked)")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
