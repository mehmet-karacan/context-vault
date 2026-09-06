"""Compatibility adapter for the canonical discovery pipeline (Aşama 7).

This module provides the contract used by repository and archive scanners:

    discover_files(root_dir, *, include_patterns, exclude_patterns) -> List[ScannedFile]

All callers use the same fail-closed discovery implementation. Scanner errors
propagate; there is no weaker fallback that can follow a symlink or bypass an
ignore/security rule.
"""

from __future__ import annotations

from typing import List, Optional

from .discovery import discover_directory
from .ignore_rules import build_ignore_rules
from .scan_result import ScannedFile


def discover_files(
    root_dir: str,
    *,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> List[ScannedFile]:
    """Discover files under ``root_dir`` through the canonical scanner.

    Contract: returns a ``List[ScannedFile]`` with at least ``relative_path``,
    ``abs_path``, ``size_bytes``, ``content_hash`` populated so the incremental
    re-index service can make change decisions (AKTIF_GOREV.md §7.6).
    """
    ignore = build_ignore_rules(
        root_path=root_dir,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )
    result = discover_directory(root_dir, ignore=ignore)
    return [
        ScannedFile(
            relative_path=f.relative_path,
            abs_path=f.absolute_path,
            size_bytes=f.size_bytes,
            content_hash=f.content_hash,
            language=f.language,
            mime_type=f.mime_type,
            is_binary=f.is_binary,
            is_generated=f.is_generated,
            is_ignored=f.is_ignored,
            metadata_json=dict(f.metadata_json or {}),
        )
        for f in result.files
    ]
