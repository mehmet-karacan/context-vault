"""Aşama 9.5: MIME/magic-byte + size + total-limit validation tests.

Exercises ``src.infrastructure.security.file_validation`` with an injectable
config (``SimpleNamespace``) so thresholds are varied without a full Settings.
Magical signatures are built in-memory — no real PDF/PNG/DOCX needed.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.infrastructure.security.file_validation import (
    detect_magic_type,
    validate_upload,
)

CFG = SimpleNamespace(
    MAX_DOCUMENT_BYTES=1000,
    MAX_TOTAL_INGESTION_BYTES=5000,
    MIME_VALIDATION_STRICT=True,
)

_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"
_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 20
_TXT = b"Gunluk rapor: surec tamamlandi.\n"
_DOCX = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 16  # ZIP office container


def test_valid_pdf_passes():
    res = validate_upload(_PDF, "rapor.pdf", "application/pdf", config=CFG)
    assert res.ok is True
    assert res.detected_magic == "pdf"
    assert res.detected_mime == "pdf"


def test_valid_image_passes():
    res = validate_upload(_PNG, "foto.png", "image/png", config=CFG)
    assert res.ok is True
    assert res.detected_magic == "image"


def test_valid_plain_text_passes():
    res = validate_upload(_TXT, "not.txt", "text/plain", config=CFG)
    assert res.ok is True
    assert res.detected_magic is None  # plain text has no binary magic
    assert res.detected_mime == "text"


def test_valid_office_docx_passes():
    res = validate_upload(_DOCX, "belge.docx", None, config=CFG)
    assert res.ok is True
    assert res.detected_magic == "office"


def test_oversize_file_rejected():
    big = b"x" * 1500
    res = validate_upload(big, "buyuk.txt", "text/plain", config=CFG)
    assert res.ok is False
    assert "limit" in (res.error or "").lower()


def test_extension_vs_magic_mismatch_renamed_pdf_rejected():
    # A .txt that is actually a PDF must not be auto-trusted.
    res = validate_upload(_PDF, "aslinda_pdf.txt", "text/plain", config=CFG)
    assert res.ok is False
    assert res.detected_magic == "pdf"
    assert "mismatch" in (res.error or "").lower() or "content" in (res.error or "").lower()


def test_claimed_pdf_without_magic_rejected():
    # A .pdf carrying no PDF signature (text masquerading as PDF) is refused.
    res = validate_upload(_TXT, "sahte.pdf", "application/pdf", config=CFG)
    assert res.ok is False
    assert res.detected_magic is None


def test_mime_magic_conflict_rejected():
    # Real PDF bytes but MIME claims an image.
    res = validate_upload(_PDF, "rapor.pdf", "image/png", config=CFG)
    assert res.ok is False


def test_magic_detection_is_consistent():
    assert detect_magic_type(_PDF) == "pdf"
    assert detect_magic_type(_PNG) == "image"
    assert detect_magic_type(_DOCX) == "office"
    assert detect_magic_type(_TXT) is None
    assert detect_magic_type(b"") is None


def test_total_ingestion_limit_exceeded():
    # current_total_bytes near the cap: adding this file would breach it.
    res = validate_upload(
        _TXT, "not.txt", "text/plain", config=CFG, current_total_bytes=4990
    )
    assert res.ok is False
    assert "total" in (res.error or "").lower()


def test_total_within_limit_passes():
    res = validate_upload(
        _TXT, "not.txt", "text/plain", config=CFG, current_total_bytes=100
    )
    assert res.ok is True
