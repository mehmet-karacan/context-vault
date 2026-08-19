"""Shared scan result model for repository/archive/directory sources (Aşama 7).

Format types produced by the source scanners (``git_source`` /
``archive_source``) and consumed by the incremental re-index service
(``application.reindex_service``). This module deliberately owns the small
data model (not any discovery logic) so the concurrently-developed discovery
layer and this code agree on one shape.

A ``ScanResult`` describes one immutable snapshot of a source: its revision
(commit SHA for git, checksum for archives/directories) plus the list of
files discovered under the extraction sandbox. ``ScannedFile`` entries carry
enough metadata (content_hash, language, is_binary, ...) for the re-index
service to decide whether a file changed since the previous active version
(AKTIF_GOREV.md §7.4 / §7.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ScannedFile:
    """A single file discovered inside a scanned source."""

    relative_path: str
    abs_path: str
    size_bytes: int = 0
    content_hash: str = ""
    language: Optional[str] = None
    mime_type: Optional[str] = None
    is_binary: bool = False
    is_generated: bool = False
    is_ignored: bool = False
    metadata_json: Optional[dict] = None


@dataclass
class ScanResult:
    """An immutable snapshot of a scanned source plus its discovered files."""

    source_type: str  # repository | archive | directory
    source_revision: str = ""
    root_dir: str = ""
    repo_url: str = ""
    branch_or_ref: str = ""
    commit_sha: str = ""
    files: List[ScannedFile] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def file_map(self) -> dict:
        """Returns ``relative_path -> ScannedFile`` for O(1) lookups."""
        return {f.relative_path: f for f in self.files}
