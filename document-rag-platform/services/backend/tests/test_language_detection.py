"""Tests for deterministic language and binary detection (Aşama 7.4)."""

import pytest

from src.infrastructure.repositories.language_detection import (
    detect_language,
    is_binary,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("main.py", "python"),
        ("pkg/__init__.py", "python"),
        ("src/App.tsx", "typescript"),
        ("src/service.ts", "typescript"),
        ("index.js", "javascript"),
        ("component.jsx", "javascript"),
        ("Main.java", "java"),
        ("queries.sql", "sql"),
        ("pkg/pkg_body.pkb", "plsql"),
        ("pkg/pkg_spec.pks", "plsql"),
        ("config.json", "json"),
        ("ci/pipeline.yaml", "yaml"),
        ("docker-compose.yml", "yaml"),
        ("docs/README.md", "markdown"),
        ("index.html", "html"),
        ("styles.css", "css"),
        ("theme.scss", "scss"),
        ("Dockerfile", "dockerfile"),
        ("Makefile", "makefile"),
        ("deploy.sh", "shell"),
        ("run.ps1", "powershell"),
        ("model.tf", "terraform"),
        ("script.sql", "sql"),
    ],
)
def test_detect_language_by_extension(path, expected):
    assert detect_language(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "noext",
        "README",
        "file.unknown_extension",
        ".hidden",
        "archive.tar.xz.q1",
    ],
)
def test_detect_language_returns_none_for_unknown(path):
    assert detect_language(path) is None


def test_detect_language_filename_override_wins():
    assert detect_language("arbitrary/container", filename="main.py") == "python"


def test_detect_language_windows_style_path():
    assert detect_language("src\\app\\main.py") == "python"


def test_is_binary_extension_shortcut():
    assert is_binary("tool.exe", b"MZ\x90\x00")
    assert is_binary("lib.dll", b"MZ\x90\x00")
    assert is_binary("archive.zip", b"PK\x03\x04...")
    assert is_binary("data.class", b"\xca\xfe\xba\xbe")


def test_is_binary_magic_signature():
    assert is_binary("image.png", b"\x89PNG\r\n\x1a\n\x00")
    assert is_binary("photo.jpg", b"\xff\xd8\xff\xe0")
    assert is_binary("f.pdf", b"%PDF-1.7")


def test_is_binary_nul_byte_detection():
    assert is_binary("raw.bin", b"\x00\x01\x02")
    assert is_binary("mixed", b"text\x00text")


def test_is_binary_plain_text_false():
    assert not is_binary("main.py", b'print("hello")\n')


def test_is_binary_empty_probe_false():
    assert not is_binary("f.txt", b"")
