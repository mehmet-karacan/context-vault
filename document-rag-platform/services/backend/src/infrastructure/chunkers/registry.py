"""Chunker Registry (Aşama 4).

Content-sensitive, token-bounded chunking that turns a ``NormalizedSource``
(see ``domain.normalized_content``) into a flat, ordered list of
``ChunkCandidate`` objects with parent-child relationships.

Design goals / AKTIF_GOREV.md Bölüm 4:

- One ``ChunkerRegistry`` replaces a single generic ``chunk_text``. It
  dispatches each ``ContentUnit`` by its ``unit_type`` to a specialised
  chunker: document text (paragraph / list / ...) -> ``DocumentChunker``,
  ``table`` -> ``TableChunker``, ``code`` -> ``CodeChunker``.
- Token counting goes through a ``TokenCounter`` *protocol* object (``count``)
  injected via constructor. If none is provided a conservative naive
  character/4 fallback is used. The concrete ``token_counter.py`` module is
  intentionally NOT imported here (built concurrently) — the counter is only
  ever received as a duck-typed receiver.
- The raw ``content`` and the ``embedding_text`` (raw + heading-context header)
  are kept separate: ``content`` stays untouched, ``embedding_text`` carries a
  controlled heading-context prefix (Bölüm 4: "Ham içerik ile embedding'e
  gönderilen embedding_text ayrımını koru").
- Every ``ChunkCandidate`` carries parser/chunker profile + version metadata so
  "Her chunk parser, chunker ve embedding profile sürümünü taşır" holds.
- ``sequence_no`` is monotonic across the whole result; ``content_hash`` is a
  stable sha256 of ``content`` for duplicate / embedding-cache detection.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Dict, List, Optional, Sequence

from ...config import settings
from ...domain.normalized_content import NormalizedSource, UnitType
from .base import (
    ChunkCandidate,
    ChunkerProfile,
    NaiveTokenCounter,
    build_embedding_text,
    heading_header,
    merge_locators,
)

# ContentUnit.unit_type values the DocumentChunker treats as document text.
# Heading is *context* (it lives on the hierarchy), image/page_break carry no
# chunkable text, table/code are routed to their own chunkers.
DOC_TEXT_TYPES: frozenset = frozenset(
    {
        UnitType.PARAGRAPH,
        UnitType.LIST_ITEM,
        UnitType.FORMULA,
        UnitType.IMAGE_CAPTION,
        UnitType.OCR_TEXT,
        UnitType.FILE_HEADER,
        UnitType.SYMBOL,
        UnitType.CONFIGURATION,
    }
)

__all__ = [
    "ChunkCandidate",
    "ChunkerProfile",
    "NaiveTokenCounter",
    "heading_header",
    "build_embedding_text",
    "merge_locators",
    "ChunkerRegistry",
]


class ChunkerRegistry:
    """Top-level content-sensitive chunker (conforms to ``Chunker`` port)."""

    def __init__(
        self,
        token_counter: Optional[Any] = None,
        *,
        target_tokens: Optional[int] = None,
        min_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
        overlap_ratio: Optional[float] = None,
        parent_max_tokens: Optional[int] = None,
        chunker_profile: Optional[ChunkerProfile] = None,
    ):
        """Builds the chunkers, resolving config from injected values or Settings.

        ``token_counter`` accepts any object exposing ``count(text) -> int``
        (a ``TokenCounter``-protocol receiver). It is never imported from the
        concrete ``token_counter`` module, so this module stays independent of
        the concurrently-built implementation and falls back to a naive
        character/4 counter when none is supplied.
        """
        injected = token_counter is not None
        self.token_counter = token_counter if injected else NaiveTokenCounter()
        self.profile = chunker_profile or ChunkerProfile(
            token_counter_method="injected" if injected else "fallback"
        )

        self.target_tokens = (
            target_tokens if target_tokens is not None else settings.CHUNK_TARGET_TOKENS
        )
        self.min_tokens = (
            min_tokens if min_tokens is not None else settings.CHUNK_MIN_TOKENS
        )
        self.max_tokens = (
            max_tokens if max_tokens is not None else settings.CHUNK_MAX_TOKENS
        )
        self.overlap_ratio = (
            overlap_ratio
            if overlap_ratio is not None
            else settings.CHUNK_OVERLAP_RATIO
        )
        self.parent_max_tokens = (
            parent_max_tokens
            if parent_max_tokens is not None
            else settings.PARENT_CHUNK_MAX_TOKENS
        )

        # Lazy imports avoid a module-load cycle: chunker modules import the
        # shared definitions from this module (base helpers live here).
        from .code_chunker import CodeChunker
        from .document_chunker import DocumentChunker
        from .table_chunker import TableChunker

        self._document_chunker = DocumentChunker(
            counter=self.token_counter,
            min_tokens=self.min_tokens,
            max_tokens=self.max_tokens,
            overlap_ratio=self.overlap_ratio,
            chunker_profile=self.profile,
        )
        self._table_chunker = TableChunker(
            counter=self.token_counter,
            max_tokens=self.max_tokens,
            chunker_profile=self.profile,
        )
        self._code_chunker = CodeChunker(
            counter=self.token_counter,
            max_tokens=self.max_tokens,
            chunker_profile=self.profile,
        )

    # --- dispatch ---------------------------------------------------------

    def _segments(self, content: NormalizedSource) -> List[tuple]:
        """Splits units into ordered segments of a single chunking family.

        Returns a list of ``("doc", [units]) | ("table", unit) | ("code", unit)``
        preserving the original reading order so mixed documents are chunked in
        document order.
        """
        segments: List[tuple] = []
        for unit in content.units:
            ut = unit.unit_type
            if ut is UnitType.TABLE:
                segments.append(("table", unit))
            elif ut is UnitType.CODE:
                segments.append(("code", unit))
            elif ut in DOC_TEXT_TYPES:
                if segments and segments[-1][0] == "doc":
                    segments[-1][1].append(unit)
                else:
                    segments.append(("doc", [unit]))
            # HEADING / IMAGE / PAGE_BREAK: not chunkable content themselves.
            # Heading context is carried by each unit's hierarchy.heading_path
            # and injected into embedding_text by the document chunker.
        return segments

    def chunk(self, content: NormalizedSource, **options: Any) -> List[ChunkCandidate]:
        """Chunks every unit of ``content`` into ordered ``ChunkCandidate``s."""
        children: List[ChunkCandidate] = []
        for kind, payload in self._segments(content):
            if kind == "doc":
                children.extend(self._document_chunker.chunk(payload, content))
            elif kind == "table":
                children.extend(self._table_chunker.chunk(payload, content))
            elif kind == "code":
                children.extend(self._code_chunker.chunk(payload, content))

        return self._finalize(children, content.source_id)

    # --- parent-child + ordering -----------------------------------------

    def _attach_parents(
        self, children: Sequence[ChunkCandidate], source_id: str
    ) -> List[ChunkCandidate]:
        """Aggregates consecutive children into parent chunks (Bölüm 4).

        Parent chunks group neighbouring children up to
        ``PARENT_CHUNK_MAX_TOKENS`` while respecting heading-section
        boundaries. Each child's ``parent_chunk_id`` points to the parent that
        aggregates it; parents are appended after the children.
        """
        parents: List[ChunkCandidate] = []
        bucket: List[ChunkCandidate] = []
        bucket_key: Optional[tuple] = None
        bucket_tokens = 0
        parent_index = 0

        def flush() -> None:
            nonlocal bucket, bucket_key, bucket_tokens, parent_index
            if not bucket:
                return
            parent_index += 1
            pid = f"{source_id}:parent:{parent_index}"
            content = "\n\n".join(c.content for c in bucket)
            heading_path = list(bucket[0].heading_path)
            embedding_text = build_embedding_text(heading_path, content)
            parent = ChunkCandidate(
                chunk_id=pid,
                source_id=source_id,
                chunk_type="parent",
                content=content,
                embedding_text=embedding_text,
                heading_path=heading_path,
                locator=merge_locators([c.locator for c in bucket]),
                parent_chunk_id=None,
                content_hash=self._content_hash(content),
                token_count=self.token_counter.count(content),
                order=0,
                metadata={
                    "chunker_profile": self.profile.to_dict(),
                    "chunk_count": len(bucket),
                    "aggregates": [c.chunk_id for c in bucket],
                },
                unit_ids=[uid for c in bucket for uid in c.unit_ids],
            )
            for child in bucket:
                child.parent_chunk_id = pid
            parents.append(parent)
            bucket = []
            bucket_key = None
            bucket_tokens = 0

        for child in children:
            key = tuple(child.heading_path or [])
            tokens = self.token_counter.count(child.content)
            if bucket and (
                bucket_key != key
                or bucket_tokens + tokens > self.parent_max_tokens
            ):
                flush()
            bucket.append(child)
            bucket_key = key
            bucket_tokens += tokens
        flush()

        return list(children) + parents

    def _finalize(
        self, children: Sequence[ChunkCandidate], source_id: str
    ) -> List[ChunkCandidate]:
        result = self._attach_parents(children, source_id)
        for i, candidate in enumerate(result, start=1):
            candidate.sequence_no = i
            candidate.order = i
        return result

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256((content or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _new_candidate_id(source_id: str, chunk_type: str) -> str:
        return f"{source_id}:{chunk_type}:{uuid.uuid4().hex[:12]}"
