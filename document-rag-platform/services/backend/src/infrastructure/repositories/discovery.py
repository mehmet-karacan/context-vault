"""Directory source discovery (Aşama 7.4).

Walks a directory recursively and produces ``DiscoveredFile`` descriptors that
are shaped like the ``SourceFile`` model (relative_path, language, mime_type,
size_bytes, content_hash, is_binary, is_generated, is_ignored, metadata_json).

The walk enforces the documented safety limits (AKTIF_GOREV.md §7.2 / §11):

- ``CODE_MAX_FILES`` — hard cap on how many files one scan may discover.
- ``CODE_MAX_TOTAL_BYTES`` — hard cap on total bytes across discovered files.
- ``CODE_MAX_FILE_BYTES`` — per-file cap; larger files are skipped.
- ``CODE_SCAN_TIMEOUT_SECONDS`` — wall-clock budget that aborts the walk.
- ``CODE_FOLLOW_SYMLINKS`` (default False) — symlinks are never traversed.
- ``CODE_SECRET_POLICY`` (default ``skip``) — sensitive paths are skipped.

All limits come from ``ScanConfig`` (built from ``Settings``); none are
hardcoded here. The walker is injectable for pure/filesystem-free tests.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple

from ...config import Settings, settings as default_settings
from .ignore_rules import (
    IgnoreRules,
    build_ignore_rules,
    is_generated_file,
    is_sensitive_path,
)
from .language_detection import detect_language, is_binary

CHUNK_SIZE = 65536
_PROBE_SIZE = 8192


class _LimitsReached(Exception):
    """Internal signal to abort the walk when a discovery limit is hit."""


@dataclass
class ScanConfig:
    """Configurable limits/knobs for discovery (no hardcoded numbers in logic)."""

    max_files: int = 20000
    max_total_bytes: int = 1073741824
    max_file_bytes: int = 2097152
    scan_timeout_seconds: float = 900.0
    follow_symlinks: bool = False
    allow_submodules: bool = False
    allow_git_lfs: bool = False
    secret_policy: str = "skip"

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None) -> "ScanConfig":
        s = settings or default_settings
        return cls(
            max_files=int(getattr(s, "CODE_MAX_FILES", 20000)),
            max_total_bytes=int(getattr(s, "CODE_MAX_TOTAL_BYTES", 1073741824)),
            max_file_bytes=int(getattr(s, "CODE_MAX_FILE_BYTES", 2097152)),
            scan_timeout_seconds=float(
                getattr(s, "CODE_SCAN_TIMEOUT_SECONDS", 900)
            ),
            follow_symlinks=bool(getattr(s, "CODE_FOLLOW_SYMLINKS", False)),
            allow_submodules=bool(getattr(s, "CODE_ALLOW_SUBMODULES", False)),
            allow_git_lfs=bool(getattr(s, "CODE_ALLOW_GIT_LFS", False)),
            secret_policy=str(getattr(s, "CODE_SECRET_POLICY", "skip")),
        )


@dataclass
class DiscoveredFile:
    """A discovered source file, shaped like the ``SourceFile`` model."""

    relative_path: str
    absolute_path: str
    language: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: int = 0
    content_hash: str = ""
    is_binary: bool = False
    is_generated: bool = False
    is_ignored: bool = False
    metadata_json: dict = field(default_factory=dict)

    def as_sourcefile_dict(self) -> dict:
        """Return a plain dict matching the ``SourceFile`` model columns."""
        return {
            "relative_path": self.relative_path,
            "language": self.language,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "is_binary": self.is_binary,
            "is_generated": self.is_generated,
            "is_ignored": self.is_ignored,
            "metadata_json": self.metadata_json,
        }


@dataclass
class ScanResult:
    files: List[DiscoveredFile] = field(default_factory=list)
    truncated: bool = False
    reason: Optional[str] = None
    total_bytes: int = 0


# The default walker yields `(dirpath, dirnames, filenames)` triples like
# os.walk(topdown=True). It is a named type so tests can inject a fake.
Walker = Callable[[str], Iterator[Tuple[str, List[str], List[str]]]]


def _default_walker(root: str) -> Iterator[Tuple[str, List[str], List[str]]]:
    for dirpath, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        dirnames.sort()
        filenames.sort()
        yield dirpath, dirnames, filenames


def _guess_mime(rel: str) -> Optional[str]:
    mime, _ = mimetypes.guess_type(rel)
    return mime


def _read_file_metadata(abs_path: str, max_bytes: int) -> Optional[dict]:
    """Hash a file and probe its first bytes. Returns None if not readable."""
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return None
    if size > max_bytes:
        return {"skipped_large": True, "size_bytes": size}
    sha = hashlib.sha256()
    first_bytes = b""
    try:
        with open(abs_path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                hasher_local = sha
                hasher_local.update(chunk)
                if len(first_bytes) < _PROBE_SIZE:
                    first_bytes += chunk[:_PROBE_SIZE - len(first_bytes)]
    except OSError:
        return None
    return {
        "size_bytes": size,
        "content_hash": sha.hexdigest(),
        "first_bytes": first_bytes,
    }


def discover_directory(
    path: str,
    ignore: Optional[IgnoreRules] = None,
    config: Optional[ScanConfig] = None,
    walker: Optional[Walker] = None,
) -> ScanResult:
    """Recursively discover source files under ``path``.

    ``path`` must already have been validated (e.g. by ``is_allowed_scan_path``)
    as an allowed, canonical directory. Returns a ``ScanResult`` containing the
    discovered descriptors plus truncation metadata; the descriptors never list
    ignored, sensitive, oversize, or symlinked files.
    """
    cfg = config or ScanConfig.from_settings()
    if ignore is None:
        ignore = build_ignore_rules(root_path=path)
    walk = walker or _default_walker

    secret_skip = (cfg.secret_policy or "skip").strip().lower() == "skip"
    result = ScanResult()
    deadline = time.monotonic() + max(float(cfg.scan_timeout_seconds), 0.0)

    try:
        for dirpath, dirnames, filenames in walk(path):
            if time.monotonic() > deadline:
                result.truncated = True
                result.reason = "scan_timeout"
                raise _LimitsReached()

            dir_rel = _posix_rel(path, dirpath)

            pruned = []
            for name in dirnames:
                child = os.path.join(dirpath, name)
                child_rel = dir_rel + "/" + name if dir_rel else name
                if not cfg.follow_symlinks and os.path.islink(child):
                    continue
                if ignore.is_ignored(child_rel, is_dir=True):
                    continue
                pruned.append(name)
            dirnames[:] = pruned

            for name in filenames:
                abs_path = os.path.join(dirpath, name)
                rel = dir_rel + "/" + name if dir_rel else name

                if not cfg.follow_symlinks and os.path.islink(abs_path):
                    continue
                if ignore.is_ignored(rel):
                    continue
                if secret_skip and is_sensitive_path(rel):
                    continue

                info = _read_file_metadata(abs_path, cfg.max_file_bytes)
                if info is None:
                    continue
                if info.get("skipped_large"):
                    result.truncated = True
                    result.reason = "file_too_large"
                    continue

                if result.total_bytes + info["size_bytes"] > cfg.max_total_bytes:
                    result.truncated = True
                    result.reason = "total_bytes"
                    raise _LimitsReached()

                first_bytes = info["first_bytes"]
                descriptor = DiscoveredFile(
                    relative_path=rel,
                    absolute_path=abs_path,
                    language=detect_language(rel),
                    mime_type=_guess_mime(rel),
                    size_bytes=info["size_bytes"],
                    content_hash=info["content_hash"],
                    is_binary=is_binary(rel, first_bytes),
                    is_generated=is_generated_file(rel),
                    is_ignored=False,
                    metadata_json={
                        "source": "directory",
                        "scan_root": os.path.realpath(path),
                    },
                )
                result.files.append(descriptor)
                result.total_bytes += info["size_bytes"]

                if len(result.files) >= cfg.max_files:
                    result.truncated = True
                    result.reason = "max_files"
                    raise _LimitsReached()
    except _LimitsReached:
        pass

    return result


def _posix_rel(root: str, target: str) -> str:
    try:
        rel = os.path.relpath(target, root)
    except ValueError:
        rel = ""
    rel = rel.replace("\\", "/")
    if rel == ".":
        return ""
    return rel
