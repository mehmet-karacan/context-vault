"""MIME / magic-byte file validation (Aşama 9.5).

``validate_upload`` is the reusable, DB-free gate the upload endpoint calls
before accepting a document. It enforces (AKTIF_GOREV.md §9.5):

- **Magic-byte detection** — a lightweight, dependency-free signature sniffer
  (``detect_magic_type``). It deliberately mirrors the ParserRouter's trust
  rules (Aşama 3.1): magic bytes are ground truth for the *actual* content.
- **Extension-vs-magic consistency** — a file is never trusted blindly by its
  name/label. When the real binary signature contradicts the extension (e.g. a
  ``.txt`` that is really a PDF, or a ``.pdf`` containing no PDF signature) the
  file is refused with a safe error instead of being auto-trusted.
- **Per-file size limit** (``MAX_DOCUMENT_BYTES``) and **total ingestion byte
  limit** (``MAX_TOTAL_INGESTION_BYTES``) via ``current_total_bytes``.

The sniffer tries ``python-magic`` (``libmagic``) when it is importable and
functional, otherwise falls back to the built-in signature table, so the module
works in an offline / minimal-dependency environment.

The result is a plain dataclass (``UploadValidationResult``), so the API layer
can map a failure to a safe 400 with no stack trace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from ...config import settings


class FileValidationError(ValueError):
    """Raised when a file fails MIME/magic/size/limit validation."""


@dataclass(frozen=True)
class UploadValidationResult:
    """Outcome of validating an upload.

    ``ok`` is True only when every check passed. On failure ``error`` carries a
    short, user-safe message (never a stack trace). ``detected_mime`` /
    ``detected_magic`` are the category the file *claims* / *actually is*.
    """

    ok: bool
    error: Optional[str] = None
    detected_mime: Optional[str] = None
    detected_magic: Optional[str] = None
    size: Optional[int] = None


# --- Category vocabulary -----------------------------------------------------
# Canonical categories: "pdf", "office" (DOCX/XLSX/PPTX), "image", "text".

_TEXT_EXTENSIONS = {
    ".txt",
    ".text",
    ".md",
    ".markdown",
    ".py",
    ".pyi",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".rb",
    ".sh",
    ".bash",
    ".ps1",
    ".sql",
    ".plsql",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".css",
    ".tf",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
    ".csv",
    ".tsv",
    ".properties",
}

_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".ico",
    ".svg",
}

_OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}

_BINARY_CATEGORIES = ("pdf", "office", "image")


def _ext_category(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    ext = os.path.splitext(os.path.basename(filename))[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in _OFFICE_EXTENSIONS:
        return "office"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in _TEXT_EXTENSIONS or ext:
        return "text"
    return None


def _mime_category(mime_type: Optional[str]) -> Optional[str]:
    if not mime_type:
        return None
    mime = mime_type.split(";", 1)[0].strip().lower()
    if mime == "application/pdf":
        return "pdf"
    if (
        "openxmlformats-officedocument" in mime
        and any(t in mime for t in ("wordprocessingml", "spreadsheetml", "presentationml"))
    ):
        return "office"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("text/") or mime.startswith("application/json"):
        return "text"
    if mime.startswith("application/x-") or mime.startswith("application/xml"):
        return "text"
    return None


# --- Magic-byte sniffer ------------------------------------------------------

def detect_magic_type(file_bytes: bytes) -> Optional[str]:
    """Return the detected content category from magic bytes, else ``None``.

    ``None`` means "no recognised binary signature" (i.e. plain text or an
    unknown/untagged binary). Recognised signatures: PDF, ZIP-based office
    (DOCX/XLSX/PPTX), and the common image formats.
    """
    if not file_bytes:
        return None
    head = file_bytes[:16]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "office"  # ZIP container (DOCX/XLSX/PPTX)
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head.startswith(b"GIF8"):
        return "image"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "image"  # TIFF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head[:2] == b"BM" and head[6:10] == b"\x00\x00\x00\x00":
        return "image"  # BMP
    return None


def _try_libmagic(file_bytes: bytes) -> Optional[str]:
    """Best-effort ``python-magic`` resolution (the richer detector).

    Returns None on any failure (not installed or libmagic DLL missing) so the
    built-in sniffer is always the reliable fallback.
    """
    try:
        import magic  # type: ignore
    except Exception:
        return None
    try:
        label = magic.from_buffer(file_bytes, mime=True)
    except Exception:
        return None
    if not label:
        return None
    label = str(label).lower()
    if "pdf" in label:
        return "pdf"
    if "zip" in label or "officedocument" in label:
        return "office"
    if label.startswith("image/"):
        return "image"
    return None


def _consistency_ok(ext_cat, mime_cat, magic_cat, strict: bool) -> bool:
    """Safe-policy consistency check (AKTIF §3.1 / §9.5).

    - A recognised binary magic signature is ground truth: if the extension or
      MIME claims a *different* binary category the file is refused.
    - If no binary signature is present but a binary type is *claimed* by
      extension/MIME, the file is refused when strict (a renamed-to-text PDF, a
      text masquerading as a PDF, etc.). Text claims with no signature are fine.
    """
    if magic_cat in _BINARY_CATEGORIES:
        if ext_cat is not None and ext_cat != magic_cat:
            return False
        if mime_cat is not None and mime_cat != magic_cat:
            return False
        return True

    # No recognised binary signature present.
    ext_binary = ext_cat in _BINARY_CATEGORIES
    mime_binary = mime_cat in _BINARY_CATEGORIES
    if strict and (ext_binary or mime_binary):
        # A binary type is claimed but its magic signature is absent.
        return False
    if ext_cat is not None and mime_cat is not None and ext_binary != mime_binary:
        return False
    return True


def _default_config(config: Any) -> Any:
    return config if config is not None else settings


def validate_upload(
    file_bytes: bytes,
    filename: Optional[str] = None,
    mime_type: Optional[str] = None,
    config: Any = None,
    current_total_bytes: int = 0,
) -> UploadValidationResult:
    """Validate an upload's size, MIME/magic consistency and ingestion limits.

    All thresholds come from the injectable ``config`` (duck-typed; defaults to
    the module ``settings``) so tests can vary them.
    """
    cfg = _default_config(config)
    size = len(file_bytes or b"")

    try:
        max_doc = getattr(cfg, "MAX_DOCUMENT_BYTES", 20971520)
        max_total = getattr(cfg, "MAX_TOTAL_INGESTION_BYTES", 1073741824)
        strict = bool(getattr(cfg, "MIME_VALIDATION_STRICT", True))
    except Exception:
        max_doc, max_total, strict = 20971520, 1073741824, True

    if size > max_doc:
        return UploadValidationResult(
            ok=False,
            error=f"File size {size} bytes exceeds the {max_doc} byte limit",
            detected_mime=_mime_category(mime_type),
            detected_magic=None,
            size=size,
        )

    if current_total_bytes > 0 and (current_total_bytes + size) > max_total:
        return UploadValidationResult(
            ok=False,
            error=f"Upload would exceed the {max_total} byte total ingestion limit",
            detected_mime=_mime_category(mime_type),
            detected_magic=None,
            size=size,
        )

    magic_cat = detect_magic_type(file_bytes) or _try_libmagic(file_bytes)
    ext_cat = _ext_category(filename)
    mime_cat = _mime_category(mime_type)

    if not _consistency_ok(ext_cat, mime_cat, magic_cat, strict):
        label = filename or (mime_type or "file")
        return UploadValidationResult(
            ok=False,
            error=(
                f"File content does not match its declared type ({label}); "
                "refusing to auto-trust a mismatched file"
            ),
            detected_mime=mime_cat,
            detected_magic=magic_cat,
            size=size,
        )

    return UploadValidationResult(
        ok=True,
        detected_mime=mime_cat,
        detected_magic=magic_cat,
        size=size,
    )
