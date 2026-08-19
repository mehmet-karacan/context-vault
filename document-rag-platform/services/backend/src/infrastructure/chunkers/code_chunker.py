"""Code chunker (Aşama 4).

Treats a code block as an atomic-ish unit and never slices it like a
paragraph. If the whole code unit fits within ``CHUNK_MAX_TOKENS`` it is kept
as a single chunk; if it is larger it is split only at structural boundaries
(blank-line separated logical blocks), keeping full lines intact. Real
AST/symbol-aware chunking arrives in Aşama 7 (tree-sitter); this is the
deliberately simple, robust stage-4 behaviour.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, List, Optional

from ...config import settings
from ...domain.normalized_content import ContentUnit, NormalizedSource
from .base import (
    ChunkCandidate,
    ChunkerProfile,
    NaiveTokenCounter,
    build_embedding_text,
)

__all__ = ["CodeChunker"]


class CodeChunker:
    """Chunks a single ``CODE`` ContentUnit into whole-line, whole-block chunks."""

    def __init__(
        self,
        counter: Optional[Any] = None,
        *,
        max_tokens: Optional[int] = None,
        chunker_profile: Optional[ChunkerProfile] = None,
    ):
        self.token_counter = counter if counter is not None else NaiveTokenCounter()
        self.max_tokens = (
            max_tokens if max_tokens is not None else settings.CHUNK_MAX_TOKENS
        )
        self.profile = chunker_profile or ChunkerProfile()

    def chunk(self, unit: ContentUnit, source: NormalizedSource) -> List[ChunkCandidate]:
        code = unit.text if unit.text else (unit.markdown or "")
        if not code.strip():
            return []
        heading_path = (
            list(unit.hierarchy.heading_path) if unit.hierarchy else []
        )
        locator = unit.locator.to_dict() if unit.locator else {}
        language = unit.metadata.get("language")

        if self.token_counter.count(code) <= self.max_tokens:
            return [
                self._make_chunk(
                    source=source,
                    unit=unit,
                    content=code,
                    language=language,
                    heading_path=heading_path,
                    locator=locator,
                )
            ]

        # Oversized: split at blank-line block boundaries, lines stay whole.
        lines = code.splitlines()
        if not lines:
            return []
        blocks = self._split_blocks(lines)

        chunks: List[ChunkCandidate] = []
        current: List[str] = []
        current_tokens = 0
        for block in blocks:
            block_tokens = self.token_counter.count("\n".join(block))
            if current and current_tokens + block_tokens > self.max_tokens:
                chunks.append(
                    self._make_chunk(
                        source=source,
                        unit=unit,
                        content="\n".join(current),
                        language=language,
                        heading_path=heading_path,
                        locator=locator,
                    )
                )
                current = []
                current_tokens = 0
            current.extend(block)
            current_tokens += block_tokens

        if current:
            chunks.append(
                self._make_chunk(
                    source=source,
                    unit=unit,
                    content="\n".join(current),
                    language=language,
                    heading_path=heading_path,
                    locator=locator,
                )
            )
        return chunks or [
            self._make_chunk(
                source=source,
                unit=unit,
                content=code,
                language=language,
                heading_path=heading_path,
                locator=locator,
            )
        ]

    @staticmethod
    def _split_blocks(lines: List[str]) -> List[List[str]]:
        """Groups lines into logical blocks separated by blank lines."""
        blocks: List[List[str]] = []
        current: List[str] = []
        for line in lines:
            if line.strip():
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)
        return blocks

    def _make_chunk(
        self,
        *,
        source: NormalizedSource,
        unit: ContentUnit,
        content: str,
        language: Optional[str],
        heading_path: List[str],
        locator: dict,
    ) -> ChunkCandidate:
        embedding_text = build_embedding_text(heading_path, content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ChunkCandidate(
            chunk_id=f"{source.source_id}:code:{uuid.uuid4().hex[:12]}",
            source_id=source.source_id,
            version_id=source.version_id,
            chunk_type="code",
            content=content,
            embedding_text=embedding_text,
            heading_path=heading_path,
            locator=locator,
            content_hash=content_hash,
            metadata={
                "chunker_profile": self.profile.to_dict(),
                "language": language,
                "unit_id": unit.unit_id,
            },
            unit_ids=[unit.unit_id],
            token_count=self.token_counter.count(content),
        )
