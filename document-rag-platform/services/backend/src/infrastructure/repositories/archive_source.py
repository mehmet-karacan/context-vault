"""Archive (ZIP / TAR.GZ) source scanner (Aşama 7).

``ArchiveSourceScanner`` extracts an uploaded archive into a throwaway sandbox
with two classes of guard (AKTIF_GOREV.md §7.2 — "Archive path traversal ve
zip bomb koruması uygula"):

- **Path traversal.** Every member's resolved path must stay strictly inside
  the extraction root. A member whose canonical path would escape the root
  (``../evil``, an absolute path, ``a/../../evil``, or an unsafe tar symlink)
  is skipped (not extracted) and recorded in ``warnings``.
- **Zip bomb / limit guards.** ``max_entries``, ``max_entry_bytes`` and
  ``max_total_bytes`` bound the archive. Exceeding the entry/total limits
  refuses the whole archive (``ArchiveLimitError``); an individual oversized
  entry is skipped like an oversized file. Configured from ``Settings``
  (``CODE_ARCHIVE_*``).

The scanner is pure enough to unit test with crafted malicious archives — no
real bomb required, only the guards.
"""

from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
import zipfile
from typing import Callable, List, Optional

from ...config import settings
from .discovery_compat import discover_files as _default_discover
from .scan_result import ScanResult, ScannedFile


class ArchiveError(RuntimeError):
    """Base error for archive extraction refusal."""


class ArchiveLimitError(ArchiveError):
    """Archive exceeds entry-count or total-size limits (zip bomb guard)."""


class ArchiveSourceScanner:
    """Extracts a ZIP/TAR archive with traversal + bomb guards.

    Constructor injection keeps it unit-testable (see
    ``tests/test_archive_source.py``).
    """

    def __init__(
        self,
        *,
        max_total_bytes: Optional[int] = None,
        max_entry_bytes: Optional[int] = None,
        max_entries: Optional[int] = None,
        sandbox_factory: Optional[Callable[[], str]] = None,
        discovery: Optional[Callable[..., List[ScannedFile]]] = None,
    ):
        self.max_total_bytes = (
            max_total_bytes if max_total_bytes is not None else settings.CODE_ARCHIVE_MAX_TOTAL_BYTES
        )
        self.max_entry_bytes = (
            max_entry_bytes if max_entry_bytes is not None else settings.CODE_ARCHIVE_MAX_ENTRY_BYTES
        )
        self.max_entries = max_entries if max_entries is not None else settings.CODE_ARCHIVE_MAX_ENTRIES
        self.sandbox_factory = sandbox_factory or (lambda: tempfile.mkdtemp(prefix="code-archive-"))
        self.discovery = discovery or _default_discover
        self.warnings: List[str] = []

    def _is_within_root(self, root: str, target: str) -> bool:
        root_real = os.path.realpath(root)
        target_real = os.path.realpath(target)
        return target_real == root_real or target_real.startswith(root_real + os.sep)

    def _resolve_member(self, root: str, member_name: str) -> Optional[str]:
        """Returns the extraction target for ``member_name``, or ``None`` if
        the member is unsafe (path traversal / absolute path / empty)."""
        if not member_name or member_name in (".", "/"):
            return None
        norm = member_name.replace("\\", "/")
        if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
            return None  # absolute member path
        target = os.path.join(root, *[p for p in norm.split("/") if p not in ("", ".")])
        if not self._is_within_root(root, target):
            return None  # escapes the extraction root
        return os.path.realpath(target)

    def extract(self, archive_path: str, dest_dir: str) -> List[str]:
        """Extracts ``archive_path`` into ``dest_dir`` with all guards, and
        returns the list of extracted relative paths.

        Raises ``ArchiveLimitError`` if entry count or total size is exceeded;
        traversal and oversized-single-entry members are skipped (recorded in
        ``self.warnings``).
        """
        self.warnings = []
        extracted: List[str] = []
        total = 0

        # Reliable format detection: zipfile.is_zipfile scans the End-of-Central
        # Directory record, so it never misfires on tar/gzip payloads.
        if zipfile.is_zipfile(archive_path):
            self._extract_zip(archive_path, dest_dir, extracted, total)
        else:
            self._extract_tar(archive_path, dest_dir, extracted, total)
        return extracted

    def _extract_zip(self, archive_path, dest_dir, extracted, total) -> None:
        with zipfile.ZipFile(archive_path) as zf:
            infos = zf.infolist()
            if len(infos) > self.max_entries:
                raise ArchiveLimitError(
                    f"archive member count {len(infos)} exceeds limit {self.max_entries}"
                )
            root_real = os.path.realpath(dest_dir)
            for info in infos:
                if info.is_dir():
                    continue
                target = self._resolve_member(dest_dir, info.filename)
                if target is None:
                    self.warnings.append(f"blocked path traversal member: {info.filename}")
                    continue
                if info.file_size > self.max_entry_bytes:
                    self.warnings.append(
                        f"skipped oversized entry {info.filename} ({info.file_size} bytes)"
                    )
                    continue
                with zf.open(info) as fh:
                    data = fh.read()
                total += len(data)
                if total > self.max_total_bytes:
                    raise ArchiveLimitError(
                        f"archive total {total} bytes exceeds limit {self.max_total_bytes}"
                    )
                os.makedirs(os.path.dirname(target), exist_ok=True) if os.path.dirname(target) else None
                with open(target, "wb") as out:
                    out.write(data)
                extracted.append(os.path.relpath(target, root_real).replace(os.sep, "/"))

    def _extract_tar(self, archive_path, dest_dir, extracted, total) -> None:
        root_real = os.path.realpath(dest_dir)
        count = 0
        with tarfile.open(archive_path, "r:*") as tf:
            for member in tf.getmembers():
                count += 1
                if count > self.max_entries:
                    raise ArchiveLimitError(
                        f"archive member count {count} exceeds limit {self.max_entries}"
                    )
                if member.isdir():
                    continue
                if member.issym() or member.islnk():
                    # Refuse link members outright: they may point outside root.
                    self.warnings.append(f"blocked link member: {member.name}")
                    continue
                target = self._resolve_member(dest_dir, member.name)
                if target is None:
                    self.warnings.append(f"blocked path traversal member: {member.name}")
                    continue
                if member.size > self.max_entry_bytes:
                    self.warnings.append(
                        f"skipped oversized entry {member.name} ({member.size} bytes)"
                    )
                    continue
                fobj = tf.extractfile(member)
                if fobj is None:
                    continue
                data = fobj.read()
                total += len(data)
                if total > self.max_total_bytes:
                    raise ArchiveLimitError(
                        f"archive total {total} bytes exceeds limit {self.max_total_bytes}"
                    )
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(target, "wb") as out:
                    out.write(data)
                extracted.append(os.path.relpath(target, root_real).replace(os.sep, "/"))

    def _checksum(self, archive_bytes: bytes) -> str:
        return hashlib.sha256(archive_bytes).hexdigest()

    def scan(
        self,
        archive_bytes: bytes,
        filename: Optional[str] = None,
        *,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        work: Optional[str] = None,
    ) -> ScanResult:
        """Extracts ``archive_bytes`` into a sandbox and returns a ``ScanResult``.

        ``source_revision`` is the SHA-256 checksum of the archive bytes.
        """
        sandbox = work or self.sandbox_factory()
        os.makedirs(sandbox, exist_ok=True)
        archive_path = os.path.join(sandbox, os.path.basename(filename or "archive"))
        with open(archive_path, "wb") as fh:
            fh.write(archive_bytes)
        self.extract(archive_path, sandbox)
        files = self.discovery(
            sandbox,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )
        return ScanResult(
            source_type="archive",
            source_revision=self._checksum(archive_bytes),
            root_dir=sandbox,
            files=files,
            warnings=list(self.warnings),
        )
