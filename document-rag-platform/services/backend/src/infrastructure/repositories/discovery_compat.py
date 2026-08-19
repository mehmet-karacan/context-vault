"""Discovery adapter + minimal safe fallback walker (Aşama 7).

The full discovery/ignore/language pipeline is built concurrently in
``infrastructure.repositories.discovery`` (another worker owns it — this file
never modifies it). This module provides the *contract* those scanners use:

    discover_files(root_dir, *, include_patterns, exclude_patterns) -> List[ScannedFile]

It first tries to delegate to the concurrent ``discovery`` module. If that
module is not yet present (or its API changes / errors), it falls back to a
small, self-contained safe walker so the git/archive sources and the
incremental re-index service remain functional and testable either way. The
fallback intentionally applies the same default ignore set from
AKTIF_GOREV.md §7.3 and never follows symlinks out of the root (§7.2).
"""

from __future__ import annotations

import hashlib
import os
from typing import List, Optional

from ...config import settings
from .scan_result import ScannedFile

# Default ignore set (AKTIF_GOREV.md §7.3). The concurrent discovery layer may
# apply a richer set; this is only the minimal safety floor used by the
# fallback walker.
_DEFAULT_IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "dist", "build",
    "target", "coverage", ".next", ".cache", "vendor", "__pycache__",
}
_DEFAULT_IGNORED_EXTS = {
    ".min.js", ".map", ".lock", ".png", ".jpg", ".jpeg", ".gif", ".pdf",
    ".exe", ".dll", ".so", ".class", ".jar", ".zip", ".tar", ".gz",
}
_SENSITIVE_NAMES = (".env", ".pem", ".key", ".p12", ".jks", "id_rsa")


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _guess_language(relative_path: str) -> Optional[str]:
    ext = os.path.splitext(relative_path)[1].lower().lstrip(".")
    return {"py": "python", "java": "java", "js": "javascript", "ts": "typescript",
            "json": "json", "yaml": "yaml", "yml": "yaml", "sql": "sql",
            "md": "markdown", "plsql": "plsql", "pks": "plsql", "pkb": "plsql"}.get(ext)


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _walk_fallback(
    root_dir: str,
    *,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> List[ScannedFile]:
    root_real = os.path.realpath(root_dir)
    max_file = int(getattr(settings, "CODE_MAX_FILE_BYTES", 2097152))
    files: List[ScannedFile] = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [
            d for d in dirnames
            if d not in _DEFAULT_IGNORED_DIRS
            and not (os.path.realpath(os.path.join(dirpath, d)) != root_real
                     and not os.path.realpath(os.path.join(dirpath, d)).startswith(
                         root_real + os.sep))
        ]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root_real).replace(os.sep, "/")
            base = name.lower()
            if base in _SENSITIVE_NAMES or base.endswith(_SENSITIVE_NAMES) or \
               base.startswith(".env"):
                continue
            ext = os.path.splitext(base)[1]
            if ext in _DEFAULT_IGNORED_EXTS:
                continue
            if name.endswith((".pem", ".key", ".p12", ".jks")):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > max_file:
                continue
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            binary = _is_binary(data)
            if binary:
                continue
            files.append(
                ScannedFile(
                    relative_path=rel,
                    abs_path=full,
                    size_bytes=size,
                    content_hash=_content_hash(data),
                    language=_guess_language(rel),
                    is_binary=False,
                    is_generated=False,
                    is_ignored=False,
                    metadata_json={},
                )
            )
    return files


def discover_files(
    root_dir: str,
    *,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> List[ScannedFile]:
    """Discovers files under ``root_dir``, delegating to the concurrent
    ``discovery`` module when available.

    Contract: returns a ``List[ScannedFile]`` with at least ``relative_path``,
    ``abs_path``, ``size_bytes``, ``content_hash`` populated so the incremental
    re-index service can make change decisions (AKTIF_GOREV.md §7.6).
    """
    try:
        from . import discovery as _conc  # concurrent module (may be absent)
        if hasattr(_conc, "discover_directory"):
            result = _conc.discover_directory(root_dir)
            return [
                ScannedFile(
                    relative_path=f.relative_path,
                    abs_path=f.absolute_path,
                    size_bytes=f.size_bytes,
                    content_hash=f.content_hash,
                    language=f.language,
                    mime_type=f.mime_type,
                    is_binary=f.is_binary,
                    is_generated=f.is_generated,
                    is_ignored=f.is_ignored,
                    metadata_json=dict(f.metadata_json or {}),
                )
                for f in result.files
            ]
    except Exception:
        pass
    return _walk_fallback(root_dir, include_patterns=include_patterns, exclude_patterns=exclude_patterns)
