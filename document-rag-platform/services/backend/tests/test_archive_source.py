"""Aşama 7: unit tests for ``ArchiveSourceScanner`` traversal + bomb guards.

Crafted malicious archives are built purely in-memory (no real bomb needed) —
a path-traversal member (``../evil`` / absolute path), an oversized single
entry, and an archive exceeding the total-byte / entry-count limits. Guards
are asserted directly through ``extract`` and ``scan`` with a stubbed
discovery.
"""

from __future__ import annotations

import io
import os
import zipfile

import pytest

from src.infrastructure.repositories.archive_source import (
    ArchiveLimitError,
    ArchiveSourceScanner,
)
from src.infrastructure.repositories.scan_result import ScanResult, ScannedFile


def _make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _write_tmp(data: bytes, suffix: str = ".zip"):
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fh:
        fh.write(data)
        return fh.name


def _stub_discovery(result):
    def _discover(root_dir, **kwargs):
        return result
    return _discover


def test_normal_zip_extracts_and_scans(tmp_path):
    z = _make_zip({"src/a.py": "print(1)", "src/b.py": "print(2)"})
    scanner = ArchiveSourceScanner(
        discovery=_stub_discovery(
            [ScannedFile(relative_path="src/a.py", abs_path="x", content_hash="h1")]
        )
    )
    scan = scanner.scan(z, "repo.zip", work=str(tmp_path / "s"))

    assert scan.source_type == "archive"
    assert scan.source_revision == __import__("hashlib").sha256(z).hexdigest()
    assert len(scan.files) == 1
    # The real files were extracted into the sandbox before discovery ran.
    assert (tmp_path / "s" / "src" / "a.py").exists()


def test_traversal_member_is_skipped(tmp_path):
    z = _make_zip({"safe.txt": "hello", "../../evil.txt": "pwn"})
    scanner = ArchiveSourceScanner(max_entries=100)
    dest = str(tmp_path / "out")
    os.makedirs(dest)
    extracted = scanner.extract(_write_tmp(z), dest)

    assert "safe.txt" in extracted
    # The traversal member was never extracted outside the sandbox.
    assert not (tmp_path / "evil.txt").exists()
    assert any("traversal" in w and "evil" in w for w in scanner.warnings)


def test_absolute_path_member_is_skipped(tmp_path):
    z = _make_zip({"/etc/passwd": "root:x"})
    scanner = ArchiveSourceScanner()
    dest = str(tmp_path / "out")
    os.makedirs(dest)
    extracted = scanner.extract(_write_tmp(z), dest)
    assert extracted == []
    assert scanner.warnings
    assert not (tmp_path / "out" / "etc" / "passwd").exists()


def test_oversized_single_entry_is_skipped(tmp_path):
    z = _make_zip({"big.bin": b"A" * 100, "ok.txt": "fine"})
    scanner = ArchiveSourceScanner(max_entry_bytes=50)
    dest = str(tmp_path / "out")
    os.makedirs(dest)
    extracted = scanner.extract(_write_tmp(z), dest)
    assert "ok.txt" in extracted
    assert "big.bin" not in extracted
    assert any("oversized" in w and "big.bin" in w for w in scanner.warnings)


def test_archive_exceeding_total_bytes_raises(tmp_path):
    z = _make_zip({"one.txt": "A" * 10, "two.txt": "A" * 10})
    scanner = ArchiveSourceScanner(max_total_bytes=15)
    dest = str(tmp_path / "out")
    os.makedirs(dest)
    with pytest.raises(ArchiveLimitError):
        scanner.extract(_write_tmp(z), dest)


def test_archive_exceeding_entry_count_raises(tmp_path):
    z = _make_zip({"one.txt": "x", "two.txt": "y", "three.txt": "z"})
    scanner = ArchiveSourceScanner(max_entries=2)
    dest = str(tmp_path / "out")
    os.makedirs(dest)
    with pytest.raises(ArchiveLimitError):
        scanner.extract(_write_tmp(z), dest)

