"""Shared chunking primitives (Aşama 4).

Holds the ``ChunkCandidate`` output model, the conservative fallback
``TokenCounter``-protocol implementation, profile metadata and small helpers
used by every chunker. This module is deliberately a sibling of (and imported
by) ``registry.py`` / ``document_chunker.py`` / ``table_chunker.py`` /
``code_chunker.py`` so the registry can import those chunkers without a module
import cycle. The concrete ``token_counter.py`` is a separate, concurrently
built module and is never imported here — the counter is only ever received as
a duck-typed ``count(text)`` receiver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...domain.normalized_content import SourceLocator

__all__ = [
    "ChunkCandidate",
    "ChunkerProfile",
    "NaiveTokenCounter",
    "heading_header",
    "build_embedding_text",
    "merge_locators",
]


@dataclass
class ChunkerProfile:
    """Chunker profile + version carried on every chunk (Bölüm 4 metadata)."""

    name: str = "context-vault-chunker"
    version: str = "4.0.0"
    token_counter_method: str = "fallback"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "token_counter_method": self.token_counter_method,
        }


@dataclass
class ChunkCandidate:
    """A single chunk produced by the :class:`ChunkerRegistry`.

    ``content`` is the raw content; ``embedding_text`` additionally carries a
    controlled heading-context header (raw vs embedding separation). Locators,
    heading_path, parent linkage, content_hash and sequence order are all kept
    explicitly so downstream indexing / retrieval can cite and expand a chunk.
    """

    chunk_id: str
    source_id: str
    chunk_type: str  # "document" | "table" | "code" | "parent"
    content: str
    embedding_text: str
    heading_path: List[str] = field(default_factory=list)
    locator: Dict[str, Any] = field(default_factory=dict)
    parent_chunk_id: Optional[str] = None
    content_hash: str = ""
    sequence_no: int = 0
    order: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    unit_ids: List[str] = field(default_factory=list)
    version_id: Optional[str] = None
    token_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "version_id": self.version_id,
            "chunk_type": self.chunk_type,
            "content": self.content,
            "embedding_text": self.embedding_text,
            "heading_path": list(self.heading_path),
            "locator": dict(self.locator),
            "parent_chunk_id": self.parent_chunk_id,
            "content_hash": self.content_hash,
            "sequence_no": self.sequence_no,
            "order": self.order,
            "token_count": self.token_count,
            "unit_ids": list(self.unit_ids),
            "metadata": dict(self.metadata),
        }


class NaiveTokenCounter:
    """Conservative fallback ``TokenCounter`` (chars/4) — used when no counter
    is injected. Exposes the same ``count(text)`` protocol as the port and the
    separate ``token_counter`` module."""

    def __init__(self, divisor: int = 4):
        self.divisor = max(1, int(divisor))

    def count(self, text: str) -> int:
        return max(1, len(text or "") // self.divisor)


def heading_header(path: List[str]) -> str:
    """Renders a heading path as a single section string (empty if none)."""
    parts = [p for p in (path or []) if p]
    return " > ".join(parts) if parts else ""


def build_embedding_text(path: List[str], content: str) -> str:
    """Prepends a controlled heading-context header to embedding text."""
    header = heading_header(path)
    if header and content:
        return f"# {header}\n\n{content}"
    if header:
        return f"# {header}"
    return content or ""


def _get_attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def merge_locators(locators: List[Any]) -> Dict[str, Any]:
    """Merges several locators (SourceLocator or dict) into one dict.

    Numeric ranges (page/line) become the union [min..max]; non-numeric
    fields (file_path, block_index, bbox) take the first non-null value.
    """
    present = [loc for loc in locators if loc]
    if not present:
        return {}

    def pick(name: str, agg):
        vals = [_get_attr(loc, name) for loc in present if _get_attr(loc, name) is not None]
        return agg(vals) if vals else None

    return {
        "file_path": pick("file_path", lambda v: v[0]),
        "page_start": pick("page_start", min),
        "page_end": pick("page_end", max),
        "line_start": pick("line_start", min),
        "line_end": pick("line_end", max),
        "block_index": pick("block_index", lambda v: v[0]),
        "bbox": pick("bbox", lambda v: v[0]),
    }
