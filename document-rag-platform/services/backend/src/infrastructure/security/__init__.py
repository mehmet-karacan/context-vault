"""Reusable, DB-free security helpers (Aşama 9.5 "Güvenlik").

This package centralizes the file/content security validations so they can be
reused and unit-tested in isolation (no database, no network):

- ``file_validation``  — MIME/magic-byte consistency, per-file size limit and
  total-ingestion byte limit (``UploadValidationResult``).
- ``limits``           — ingestion file-count/total-byte limits plus a thin
  wrapper reusing the Aşama 7 ``ArchiveSourceScanner`` bomb/traversal guards.
- ``redaction``        — secret/credential skip & redaction policy so secrets
  never reach the embedding gateway, plus ``redact_log_field`` for logs.
- ``prompt_injection`` — heuristic scan for common prompt-injection patterns.
- ``arbitrary_path``   — canonical block for arbitrary absolute local paths,
  re-exporting the Aşama 7 ``path_security`` guard.

Every helper accepts an injectable ``config`` (duck-typed) so tests can vary
thresholds without constructing the full ``Settings``.
"""

from __future__ import annotations

from .arbitrary_path import PathBlockError, ensure_allowed_scan_path, is_path_blocked
from .file_validation import (
    FileValidationError,
    UploadValidationResult,
    detect_magic_type,
    validate_upload,
)
from .limits import (
    IngestionLimitError,
    archive_limits,
    enforce_ingestion_limits,
    scan_archive,
)
from .prompt_injection import (
    InjectionScanResult,
    PROMPT_INJECTION_FIXTURES,
    detect_prompt_injection,
)
from .redaction import (
    contains_secret,
    is_sensitive_filename,
    redact_log_field,
    redact_secrets,
)

__all__ = [
    "FileValidationError",
    "UploadValidationResult",
    "detect_magic_type",
    "validate_upload",
    "IngestionLimitError",
    "archive_limits",
    "enforce_ingestion_limits",
    "scan_archive",
    "InjectionScanResult",
    "PROMPT_INJECTION_FIXTURES",
    "detect_prompt_injection",
    "contains_secret",
    "is_sensitive_filename",
    "redact_log_field",
    "redact_secrets",
    "PathBlockError",
    "ensure_allowed_scan_path",
    "is_path_blocked",
]
