"""Aşama 9.5: reusable ingestion/archive security-limit tests.

Verifies that the reusable ``src.infrastructure.security`` helpers enforce the
Aşama 7 archive bomb / huge-entry / path-traversal guards on crafted (in-memory)
malicious archives, plus arbitrary-path blocking via the canonical
``path_security`` guard.
"""

from __future__ import annotations

import io
import os
import zipfile
from types import SimpleNamespace

import pytest

from src.infrastructure.repositories.archive_source import ArchiveLimitError
from src.infrastructure.security.arbitrary_path import PathBlockError, ensure_allowed_scan_path
from src.infrastructure.security.limits import (
    IngestionLimitError,
    enforce_ingestion_limits,
    scan_archive,
)

_LIMIT_CFG = SimpleNamespace(
    MAX_INGESTION_FILES=3,
    MAX_TOTAL_INGESTION_BYTES=100,
    CODE_ARCHIVE_MAX_TOTAL_BYTES=100,
    CODE_ARCHIVE_MAX_ENTRY_BYTES=50,
    CODE_ARCHIVE_MAX_ENTRIES=3,
    CODE_ALLOWED_ROOTS="",
)


def _make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestIngestionLimits:
    def test_within_limits_passes(self):
        enforce_ingestion_limits(total_files=2, total_bytes=50, config=_LIMIT_CFG)

    def test_file_count_exceeded_raises(self):
        with pytest.raises(IngestionLimitError):
            enforce_ingestion_limits(total_files=4, config=_LIMIT_CFG)

    def test_total_bytes_exceeded_raises(self):
        with pytest.raises(IngestionLimitError):
            enforce_ingestion_limits(total_files=1, total_bytes=200, config=_LIMIT_CFG)


class TestArchiveBombGuards:
    def test_archive_exceeding_entry_count_raises(self, tmp_path):
        z = _make_zip({"a.txt": "x", "b.txt": "y", "c.txt": "z", "d.txt": "w"})
        with pytest.raises(ArchiveLimitError):
            scan_archive(z, "bomb.zip", work=str(tmp_path / "s"), config=_LIMIT_CFG)

    def test_archive_exceeding_total_bytes_raises(self, tmp_path):
        # 3 entries x 40 bytes = 120 > max_total_bytes(100); each entry (40) is
        # under max_entry_bytes(50) so the total-bytes guard trips, not the
        # per-entry skip.
        z = _make_zip({"a.txt": "A" * 40, "b.txt": "B" * 40, "c.txt": "C" * 40})
        with pytest.raises(ArchiveLimitError):
            scan_archive(z, "fat.zip", work=str(tmp_path / "s"), config=_LIMIT_CFG)

    def test_path_traversal_members_are_blocked(self, tmp_path):
        z = _make_zip({"safe.txt": "ok", "../../evil.txt": "pwn"})
        result = scan_archive(z, "trav.zip", work=str(tmp_path / "s"), config=_LIMIT_CFG)
        assert any("evil" in w and "traversal" in w for w in result.warnings)
        assert not (tmp_path / "evil.txt").exists()

    def test_absolute_path_members_are_blocked(self, tmp_path):
        z = _make_zip({"/etc/passwd": "root:x"})
        result = scan_archive(z, "abs.zip", work=str(tmp_path / "s"), config=_LIMIT_CFG)
        assert result.files == []
        assert not (tmp_path / "s" / "etc" / "passwd").exists()


class TestArbitraryPathBlock:
    def test_absolute_path_under_root_accepted(self, tmp_path):
        root = str(tmp_path / "workspace")
        os.makedirs(root)
        target = os.path.join(root, "src")
        os.makedirs(target)
        assert ensure_allowed_scan_path(target, [root]) == target

    def test_absolute_path_outside_root_blocked(self, tmp_path):
        root = str(tmp_path / "workspace")
        outside = str(tmp_path / "elsewhere")
        os.makedirs(root)
        os.makedirs(outside)
        with pytest.raises(PathBlockError):
            ensure_allowed_scan_path(outside, [root])

    def test_relative_path_blocked(self, tmp_path):
        root = str(tmp_path)
        with pytest.raises(PathBlockError):
            ensure_allowed_scan_path("relative/path", [root])

    def test_traversal_escape_blocked(self, tmp_path):
        root = str(tmp_path / "workspace")
        os.makedirs(root)
        escaping = os.path.join(root, "..", "workspace", "..", "victim")
        with pytest.raises(PathBlockError):
            ensure_allowed_scan_path(escaping, [root])
