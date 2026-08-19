"""Document chunker (Aşama 4).

Token-based, structure-aware chunking of document text units (paragraphs, list
items, captions, ocr text, ...). It groups whole units into token-bounded
chunks, never slicing mid-unit, and injects the current heading path as a
controlled context header into ``embedding_text`` while leaving raw
``content`` untouched (Bölüm 4). A small controlled overlap between
consecutive chunks is applied without splitting a unit across the boundary.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, List, Optional, Sequence, Tuple

from ...config import settings
from ...domain.normalized_content import ContentUnit, NormalizedSource
from .base import (
    ChunkCandidate,
    ChunkerProfile,
    NaiveTokenCounter,
    build_embedding_text,
    merge_locators,
)

__all__ = ["DocumentChunker"]


class DocumentChunker:
    """Chunks a list of document text units into token-bounded chunks."""

    def __init__(
        self,
        counter: Optional[Any] = None,
        *,
        target_tokens: Optional[int] = None,
        min_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
        overlap_ratio: Optional[float] = None,
        chunker_profile: Optional[ChunkerProfile] = None,
    ):
        self.token_counter = counter if counter is not None else NaiveTokenCounter()
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
        self.overlap_tokens = max(
            0, int(self.max_tokens * self.overlap_ratio)
        )
        self.profile = chunker_profile or ChunkerProfile()

    # --- public -----------------------------------------------------------

    def chunk(
        self, units: Sequence[ContentUnit], source: NormalizedSource
    ) -> List[ChunkCandidate]:
        units = [
            u
            for u in units
            if (u.text or u.markdown or "").strip()
        ]
        return self._chunk_units(units, source)

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _render(unit: ContentUnit) -> str:
        return (unit.markdown if unit.markdown else unit.text or "").strip()

    @staticmethod
    def _heading_of(unit: ContentUnit) -> List[str]:
        if unit.hierarchy and unit.hierarchy.heading_path:
            return list(unit.hierarchy.heading_path)
        return []

    def _chunk_units(
        self, units: Sequence[ContentUnit], source: NormalizedSource
    ) -> List[ChunkCandidate]:
        chunks: List[ChunkCandidate] = []
        buffer: List[Tuple[ContentUnit, List[str]]] = []
        buffer_tokens = 0

        def flush(carry_overlap: bool) -> None:
            nonlocal buffer, buffer_tokens
            if not buffer:
                return
            chunks.append(self._make_chunk(buffer, source))
            if carry_overlap:
                tail = self._overlap_tail(buffer)
                buffer = tail
                buffer_tokens = sum(
                    self.token_counter.count(self._render(u)) for u, _ in tail
                )
            else:
                buffer = []
                buffer_tokens = 0

        in_flight = False
        for unit in units:
            txt = self._render(unit)
            tokens = self.token_counter.count(txt)
            heading = self._heading_of(unit)

            over_token = False
            over_section = False
            if buffer:
                over_section = heading and tuple(heading) != tuple(buffer[0][1])
                # Bound by the token count of the *joined* chunk content, not the
                # per-unit sum, so the reported token_count matches the bound.
                joined = "\n\n".join(
                    [self._render(u) for u, _ in buffer] + [txt]
                )
                over_token = self.token_counter.count(joined) > self.max_tokens

            if over_token or over_section:
                flush(carry_overlap=over_token and not over_section)

            buffer.append((unit, heading))
            buffer_tokens += tokens

        flush(carry_overlap=False)
        return chunks

    def _overlap_tail(
        self, buffer: List[Tuple[ContentUnit, List[str]]]
    ) -> List[Tuple[ContentUnit, List[str]]]:
        """Trailing units whose combined tokens fit the overlap budget.

        Works from the end of the sent chunk so the next chunk re-uses the
        final context without ever slicing a unit.
        """
        tail: List[Tuple[ContentUnit, List[str]]] = []
        used = 0
        for unit, heading in reversed(buffer):
            tokens = self.token_counter.count(self._render(unit))
            if used + tokens > self.overlap_tokens:
                break
            tail.append((unit, heading))
            used += tokens
        tail.reverse()
        return tail

    def _make_chunk(
        self,
        buffer: List[Tuple[ContentUnit, List[str]]],
        source: NormalizedSource,
    ) -> ChunkCandidate:
        content = "\n\n".join(self._render(u) for u, _ in buffer)
        heading_path = list(buffer[0][1])
        embedding_text = build_embedding_text(heading_path, content)
        locator = merge_locators(
            [u.locator for u, _ in buffer if u.locator]
        )
        unit_ids = [u.unit_id for u, _ in buffer]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ChunkCandidate(
            chunk_id=f"{source.source_id}:document:{uuid.uuid4().hex[:12]}",
            source_id=source.source_id,
            version_id=source.version_id,
            chunk_type="document",
            content=content,
            embedding_text=embedding_text,
            heading_path=heading_path,
            locator=locator,
            content_hash=content_hash,
            metadata={
                "chunker_profile": self.profile.to_dict(),
                "unit_count": len(buffer),
                "unit_ids": list(unit_ids),
            },
            unit_ids=unit_ids,
            token_count=self.token_counter.count(content),
        )
