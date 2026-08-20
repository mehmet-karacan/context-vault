"""Ingestion resource limits (Aşama 9.5).

Reusable, DB-free helpers that enforce the ingestion limits documented in
AKTIF_GOREV.md §9.5 / §7.2:

- ``enforce_ingestion_limits`` — file-count and total-byte ceilings for an
  ingestion batch.
- ``scan_archive`` / ``archive_limits`` — thin, config-driven wrappers that
  **reuse** the Aşama 7 ``ArchiveSourceScanner`` bomb + path-traversal guards
  instead of re-implementing them. They only add a convenience layer (config
  plumbing + raising ``IngestionLimitError`` on the same conditions the
  scanner already refuses).

The archive scanner itself already applies the guards (AKTIF §7.2 "Archive path
traversal ve zip bomb koruması uygula", "Maksimum dosya sayısı, tek dosya
boyutu, toplam byte ... limiti koy"), so this module is intentionally thin.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ...config import settings
from ..repositories.archive_source import ArchiveSourceScanner
from ..repositories.scan_result import ScanResult


class IngestionLimitError(ValueError):
    """Raised when an ingestion batch exceeds a file-count / total-byte limit."""


def _cfg(config: Any) -> Any:
    return config if config is not None else settings


def enforce_ingestion_limits(
    total_files: int = 0,
    total_bytes: int = 0,
    *,
    max_files: Optional[int] = None,
    max_total_bytes: Optional[int] = None,
    config: Any = None,
) -> None:
    """Raise ``IngestionLimitError`` if file count or total bytes exceed limits.

    Limits resolve from explicit arguments first, then from the injectable
    ``config`` (``MAX_INGESTION_FILES`` / ``MAX_TOTAL_INGESTION_BYTES``).
    """
    cfg = _cfg(config)
    if max_files is None:
        max_files = getattr(cfg, "MAX_INGESTION_FILES", 20000)
    if max_total_bytes is None:
        max_total_bytes = getattr(cfg, "MAX_TOTAL_INGESTION_BYTES", 1073741824)

    if max_files is not None and total_files > max_files:
        raise IngestionLimitError(
            f"ingestion file count {total_files} exceeds limit {max_files}"
        )
    if max_total_bytes is not None and total_bytes > max_total_bytes:
        raise IngestionLimitError(
            f"ingestion total {total_bytes} bytes exceeds limit {max_total_bytes}"
        )


def archive_limits(config: Any = None) -> dict:
    """Return the archive bomb / traversal limit knobs from config."""
    cfg = _cfg(config)
    return {
        "max_total_bytes": getattr(cfg, "CODE_ARCHIVE_MAX_TOTAL_BYTES", 1073741824),
        "max_entry_bytes": getattr(cfg, "CODE_ARCHIVE_MAX_ENTRY_BYTES", 2097152),
        "max_entries": getattr(cfg, "CODE_ARCHIVE_MAX_ENTRIES", 20000),
    }


def scan_archive(
    archive_bytes: bytes,
    filename: Optional[str] = None,
    *,
    work: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    config: Any = None,
) -> ScanResult:
    """Scan ``archive_bytes`` through the Aşama 7 guards, config-driven.

    Reuses ``ArchiveSourceScanner`` for path-traversal / entry-size / total-size
    / entry-count protection. An archive that explicitly exceeds the entry-count
    or total-size ceilings surfaces as ``ArchiveLimitError`` (a subclass of
    ``IngestionLimitError`` here via the scanner) — callers can also rely on the
    shared ``IngestionLimitError`` base to treat both failure classes uniformly.
    """
    scanner = ArchiveSourceScanner(**archive_limits(config))
    return scanner.scan(
        archive_bytes,
        filename=filename,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        work=work,
    )
