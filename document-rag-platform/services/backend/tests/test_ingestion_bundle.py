from __future__ import annotations

import hashlib

from src.application.ingestion_bundle import build_scan_bundle, parse_scan_bundle
from src.infrastructure.repositories.scan_result import ScanResult, ScannedFile


def test_bundle_is_deterministic_and_preserves_file_provenance(tmp_path) -> None:
    alpha = tmp_path / "alpha.py"
    alpha.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    notes = tmp_path / "notes.txt"
    notes.write_text("bounded context\n", encoding="utf-8")
    scan = ScanResult(
        source_type="repository",
        source_revision="abc123",
        root_dir=str(tmp_path),
        files=[
            ScannedFile(
                relative_path="notes.txt",
                abs_path=str(notes),
                size_bytes=notes.stat().st_size,
                content_hash=hashlib.sha256(notes.read_bytes()).hexdigest(),
                mime_type="text/plain",
            ),
            ScannedFile(
                relative_path="alpha.py",
                abs_path=str(alpha),
                size_bytes=alpha.stat().st_size,
                content_hash=hashlib.sha256(alpha.read_bytes()).hexdigest(),
                language="python",
                mime_type="text/x-python",
            ),
        ],
    )

    first = build_scan_bundle(scan)
    second = build_scan_bundle(scan)
    assert first == second

    normalized = parse_scan_bundle(first)
    assert normalized.source_type == "repository"
    assert [item["relative_path"] for item in normalized.metadata["source_files"]] == [
        "alpha.py",
        "notes.txt",
    ]
    content_units = [
        unit for unit in normalized.units if unit.text != unit.locator.file_path
    ]
    assert {unit.locator.file_path for unit in content_units} == {
        "alpha.py",
        "notes.txt",
    }
    assert all(unit.locator.line_start == 1 for unit in content_units)


def test_bundle_excludes_binary_ignored_and_generated_content(tmp_path) -> None:
    kept = tmp_path / "kept.txt"
    kept.write_text("kept", encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=must-not-enter-bundle", encoding="utf-8")
    binary = tmp_path / "image.bin"
    binary.write_bytes(b"\x00\x01")
    generated = tmp_path / "bundle.min.js"
    generated.write_text("generated", encoding="utf-8")
    scan = ScanResult(
        source_type="directory",
        source_revision="rev",
        files=[
            ScannedFile(relative_path="kept.txt", abs_path=str(kept)),
            ScannedFile(relative_path=".env", abs_path=str(secret), is_ignored=True),
            ScannedFile(
                relative_path="image.bin", abs_path=str(binary), is_binary=True
            ),
            ScannedFile(
                relative_path="bundle.min.js",
                abs_path=str(generated),
                is_generated=True,
            ),
        ],
    )
    bundle = build_scan_bundle(scan)
    assert b"must-not-enter-bundle" not in bundle
    assert b"image.bin" not in bundle
    assert b"bundle.min.js" not in bundle
    assert b"kept" in bundle
