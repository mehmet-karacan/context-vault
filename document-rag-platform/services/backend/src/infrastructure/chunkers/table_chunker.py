"""Table chunker (Aşama 4).

Splits large markdown pipe-tables into row-groups. A table cell is NEVER
sliced — the unit of splitting is the whole row. Each produced row-group is
re-rendered with the header (and separator) row repeated so every chunk keeps
its column meaning (Bölüm 4: "Tabloyu hücre ortasında bölme; büyük tabloları
header tekrar ederek satır gruplarına böl").
"""

from __future__ import annotations

import hashlib
import re
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

__all__ = ["TableChunker"]

_SEP_CELL_RE = re.compile(r"^:?-+:?$")


class TableChunker:
    """Chunks a single ``TABLE`` ContentUnit into header-repeated row groups."""

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
        markdown = unit.markdown or unit.text or ""
        rows = [line.strip() for line in markdown.splitlines() if line.strip()]
        if not rows:
            return []
        groups = self._group_rows(rows)
        heading_path = (
            list(unit.hierarchy.heading_path) if unit.hierarchy else []
        )
        locator = unit.locator.to_dict() if unit.locator else {}
        return [
            self._make_group_chunk(
                source=source,
                unit=unit,
                group=group,
                group_index=i,
                total_groups=len(groups),
                heading_path=heading_path,
                locator=locator,
            )
            for i, group in enumerate(groups)
        ]

    # --- row grouping -----------------------------------------------------

    @staticmethod
    def _looks_like_separator(row: str) -> bool:
        if "-" not in row:
            return False
        body = row.strip().strip("|")
        if not body:
            return False
        cells = body.split("|") if "|" in body else [body]
        return bool(cells) and all(_SEP_CELL_RE.match(cell.strip()) for cell in cells)

    def _group_rows(self, rows: List[str]) -> List[List[str]]:
        header = rows[0]
        separator = rows[1] if len(rows) > 1 and self._looks_like_separator(rows[1]) else None
        data_start = 2 if separator else 1
        data_rows = rows[data_start:]
        if not data_rows:
            return [[header] + ([separator] if separator else [])]

        groups: List[List[str]] = []
        group: List[str] = [header] + ([separator] if separator else [])
        group_tokens = sum(self.token_counter.count(r) for r in group)

        for row in data_rows:
            row_tokens = self.token_counter.count(row)
            min_rows = 2 if separator else 1  # header (+ separator) base rows
            keeps_at_least_header = len(group) > min_rows
            if keeps_at_least_header and group_tokens + row_tokens > self.max_tokens:
                groups.append(group)
                group = [header] + ([separator] if separator else [])
                group_tokens = sum(self.token_counter.count(r) for r in group)
            group.append(row)
            group_tokens += row_tokens

        groups.append(group)
        return groups

    # --- output -----------------------------------------------------------

    def _make_group_chunk(
        self,
        *,
        source: NormalizedSource,
        unit: ContentUnit,
        group: List[str],
        group_index: int,
        total_groups: int,
        heading_path: List[str],
        locator: dict,
    ) -> ChunkCandidate:
        content = "\n".join(group)
        embedding_text = build_embedding_text(heading_path, content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ChunkCandidate(
            chunk_id=f"{source.source_id}:table:{uuid.uuid4().hex[:12]}",
            source_id=source.source_id,
            version_id=source.version_id,
            chunk_type="table",
            content=content,
            embedding_text=embedding_text,
            heading_path=heading_path,
            locator=locator,
            content_hash=content_hash,
            metadata={
                "chunker_profile": self.profile.to_dict(),
                "row_group_index": group_index,
                "row_group_total": total_groups,
                "row_group_rows": len(group),
                "header_repeated": True,
                "unit_id": unit.unit_id,
            },
            unit_ids=[unit.unit_id],
            token_count=self.token_counter.count(content),
        )
