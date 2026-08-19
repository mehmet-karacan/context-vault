"""Tests for directory discovery, ignore enforcement and safety limits
(Aşama 7.2 / 7.4)."""

import hashlib
import os

import pytest

from src.infrastructure.repositories.discovery import (
    ScanConfig,
    discover_directory,
)
from src.infrastructure.repositories.directory_source import (
    DirectorySourceScanner,
)


@pytest.fixture
def tree(tmp_path):
    """Build a representative source tree for discovery tests."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "node_modules" / "lib").mkdir(parents=True)
    (repo / "build").mkdir(parents=True)

    (repo / "src" / "main.py").write_text('print("hi")\n', encoding="utf-8")
    (repo / "src" / "app.ts").write_text("const x: number = 1;\n", encoding="utf-8")
    (repo / "README.md").write_text("# title\n", encoding="utf-8")
    (repo / "package-lock.json").write_text("{}", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (repo / "node_modules" / "lib" / "index.js").write_text(
        "module.exports = {};\n", encoding="utf-8"
    )
    (repo / "build" / "out.o").write_bytes(b"\x00\x00>\x00")
    # A plain binary-like file to exercise binary sniffing.
    (repo / "src" / "blob.bin").write_bytes(b"\x00\x01\x02\x03")

    return repo


def _config(**overrides):
    defaults = {
        "max_files": 10**6,
        "max_total_bytes": 10**12,
        "max_file_bytes": 10**9,
        "scan_timeout_seconds": 300,
    }
    defaults.update(overrides)
    return ScanConfig(**defaults)


def _sha(text: [bytes, str]) -> str:
    return hashlib.sha256(
        text if isinstance(text, bytes) else text.encode("utf-8")
    ).hexdigest()


def test_discovers_source_files_and_skips_ignored(tree):
    result = discover_directory(str(tree), config=_config())

    paths = {f.relative_path for f in result.files}
    assert "src/main.py" in paths
    assert "src/app.ts" in paths
    assert "README.md" in paths
    # Ignored dirs / sensitive files / binary artifacts are excluded.
    assert not any("node_modules" in p for p in paths)
    assert not any(p == ".env" for p in paths)
    assert not any(p.startswith("build") for p in paths)


def test_descriptor_fields_populated(tree):
    result = discover_directory(str(tree), config=_config())
    by_path = {f.relative_path: f for f in result.files}

    main = by_path["src/main.py"]
    actual = (tree / "src" / "main.py").read_bytes()
    assert main.language == "python"
    assert main.mime_type == "text/x-python"
    assert main.size_bytes == len(actual)
    assert main.content_hash == _sha(actual)
    assert main.is_binary is False
    assert main.is_generated is False
    assert main.is_ignored is False
    assert isinstance(main.metadata_json, dict)

    lock = by_path["package-lock.json"]
    assert lock.is_generated is True

    blob = by_path["src/blob.bin"]
    assert blob.is_binary is True

    ts = by_path["src/app.ts"]
    assert ts.language == "typescript"


def test_max_files_limit_enforced(tree):
    config = _config(max_files=2)
    result = discover_directory(str(tree), config=config)
    assert result.truncated is True
    assert result.reason == "max_files"
    assert len(result.files) <= 2


def test_max_total_bytes_limit_enforced(tree):
    config = _config(max_total_bytes=1)  # smaller than any single file
    result = discover_directory(str(tree), config=config)
    assert result.truncated is True
    assert result.reason == "total_bytes"
    assert result.files == []


def test_max_file_bytes_skips_large_file(tree):
    big = tree / "src" / "big.py"
    big.write_text("x\n" * 5000, encoding="utf-8")
    config = _config(max_file_bytes=100)
    result = discover_directory(str(tree), config=config)
    paths = {f.relative_path for f in result.files}
    assert "src/big.py" not in paths
    assert "src/main.py" in paths


def test_scan_timeout_aborts(tree):
    config = _config(scan_timeout_seconds=-1)
    result = discover_directory(str(tree), config=config)
    assert result.truncated is True
    assert result.reason == "scan_timeout"


def test_symlink_not_followed(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir(parents=True)
    outside.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "real.py").write_text("print(1)\n", encoding="utf-8")
    (outside / "secret.txt").write_text("private\n", encoding="utf-8")

    try:
        os.symlink(str(outside), str(repo / "linked"))
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    result = discover_directory(str(repo), config=_config())
    paths = {f.relative_path for f in result.files}
    assert "src/real.py" in paths
    assert not any("linked" in p for p in paths)


def test_fake_walker_injection(tmp_path, monkeypatch):
    """Pure test: inject a filesystem walker, no real tree required."""
    fake_walker = iter(
        [
            (str(tmp_path), ["src"], ["root.txt"]),
            (str(tmp_path / "src"), [], ["a.py"]),
        ]
    )

    def walker(root):
        return fake_walker

    (tmp_path / "src").mkdir()
    (tmp_path / "root.txt").write_text("root\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")

    result = discover_directory(
        str(tmp_path), config=_config(), walker=walker
    )
    paths = {f.relative_path for f in result.files}
    assert "root.txt" in paths
    assert "src/a.py" in paths


# --- DirectorySourceScanner (port conformance) -------------------------------
def test_directory_scanner_scan_by_relative_path(tmp_path):
    workspace = tmp_path / "workspace"
    proj = workspace / "project-a" / "src"
    proj.mkdir(parents=True)
    (proj / "main.py").write_text("print(1)\n", encoding="utf-8")

    scanner = DirectorySourceScanner(
        allowed_roots=[str(workspace)], config=_config()
    )
    files = scanner.scan(
        str(workspace), relative_path="project-a", include_patterns=[]
    )
    paths = {f.relative_path for f in files}
    assert "src/main.py" in paths


def test_directory_scanner_rejects_absolute_client_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scanner = DirectorySourceScanner(
        allowed_roots=[str(workspace)], config=_config()
    )
    with pytest.raises(ValueError):
        scanner.scan(str(workspace), relative_path=str(workspace))


def test_directory_scanner_rejects_unknown_alias(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scanner = DirectorySourceScanner(
        allowed_roots=[str(workspace)], config=_config()
    )
    with pytest.raises(ValueError):
        scanner.scan("nonexistent-alias", relative_path="")


def test_directory_scanner_rejects_escape_outside_roots(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    scanner = DirectorySourceScanner(
        allowed_roots=[str(workspace)], config=_config()
    )
    with pytest.raises(PermissionError):
        scanner.scan(str(workspace), relative_path="../outside")


def test_directory_scanner_conforms_to_source_scanner_port(tree):
    from src.domain.ports import SourceScanner

    scanner = DirectorySourceScanner(
        allowed_roots=[str(tree.parent)], config=_config()
    )
    assert isinstance(scanner, SourceScanner)
    files = scanner.scan(str(tree.parent), relative_path=tree.name)
    assert files
