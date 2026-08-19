"""Code chunker (Aşama 4 + Aşama 7.5).

Stage-4 contract (unchanged): a code block is treated as an atomic-ish unit and
is **never** sliced like a paragraph. If the whole code unit fits within
``CHUNK_MAX_TOKENS`` it is kept as a single chunk; if it is larger it is split
only at structural boundaries, keeping full lines intact.

Aşama 7.5 additions:

* ``chunk_source`` — a source-level entry point  used by ``ChunkerRegistry``
  when ``source_type == "code"``. It routes PL/SQL (and ``.sql``) sources to the
  dedicated :class:`PlSqlChunker` and chunks generic code files with
  symbol/header context (file path + language + symbol + signature) fed into
  ``embedding_text``.
* Every code chunk carries symbol context (``symbol_name`` / ``symbol_type`` /
  ``signature``) in its metadata and, when a containing top-level symbol is
  known, that symbol is reflected in the embedding header.

NOTE — real tree-sitter AST/symbol parsing is deferred: this stage uses
line/pattern-based symbol detection (see ``infrastructure/parsers/code_parser.py``)
and a line/blank-block, symbol-boundary splitter. A tree-sitter based splitter
can replace the internals here later without breaking the registry contract.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, List, Optional

from ...config import settings
from ...domain.normalized_content import ContentUnit, NormalizedSource, UnitType
from .base import ChunkCandidate, ChunkerProfile, NaiveTokenCounter, build_embedding_text
from .plsql_chunker import PLSQL_LANGS, PlSqlChunker

__all__ = ["CodeChunker"]


class CodeChunker:
    """Chunks code blocks into whole-line, whole-block chunks with context."""

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

    # --- source-level path (registry, source_type == "code") ---------------

    def chunk_source(self, source: NormalizedSource) -> List[ChunkCandidate]:
        """Chunks an entire code source; delegates PL/SQL to PlSqlChunker."""
        code_units = [
            u for u in source.units if u.unit_type is UnitType.CODE and u.text.strip()
        ]
        if not code_units:
            return []
        language = source.language or source.metadata.get("language")

        if self._is_plsql(source, language):
            return PlSqlChunker(
                counter=self.token_counter,
                max_tokens=self.max_tokens,
                chunker_profile=self.profile,
            ).chunk_source(source)

        return self._chunk_generic_source(source, code_units)

    @staticmethod
    def _is_plsql(source: NormalizedSource, language: Optional[str]) -> bool:
        if language and language in PLSQL_LANGS:
            return True
        ext = (source.metadata.get("extension") or "").lower()
        return ext in ("pls", "pks", "pkb", "prc", "fnc", "trg", "vw")

    def _chunk_generic_source(
        self, source: NormalizedSource, code_units: List[ContentUnit]
    ) -> List[ChunkCandidate]:
        text = "\n".join(u.text for u in code_units)
        file_path = (
            source.metadata.get("file_path")
            or (code_units[0].locator.file_path if code_units[0].locator else None)
            or source.title
        )
        language = source.language or source.metadata.get("language")

        # symbol index from the source's SYMBOL units (1-based line ranges).
        symbols: List[dict] = []
        for u in source.units:
            if u.unit_type is UnitType.SYMBOL and u.locator:
                symbols.append(
                    {
                        "name": u.locator.symbol_name,
                        "type": u.locator.symbol_type,
                        "start": u.locator.line_start,
                        "end": u.locator.line_end,
                        "signature": u.metadata.get("signature"),
                    }
                )
        symbols.sort(key=lambda s: s["start"] or 0)

        if self.token_counter.count(text) <= self.max_tokens:
            return [
                self._make_code_chunk(
                    source=source,
                    file_path=file_path,
                    language=language,
                    content=text,
                    locator=self._whole_locator(source, file_path),
                    prefix_lines=0,
                    symbols=symbols,
                )
            ]

        lines = text.splitlines()
        blocks = self._line_blocks(lines)
        chunks: List[ChunkCandidate] = []
        current: List[str] = []
        current_start = 0
        current_tokens = 0
        for block_start, block_lines in blocks:
            block_tokens = self.token_counter.count("\n".join(block_lines))
            if current and current_tokens + block_tokens > self.max_tokens:
                chunks.append(
                    self._make_code_chunk(
                        source=source,
                        file_path=file_path,
                        language=language,
                        content="\n".join(current),
                        locator=self._locator_for(
                            source, file_path, current_start, current_start + len(current) - 1
                        ),
                        prefix_lines=current_start,
                        symbols=symbols,
                    )
                )
                current = []
                current_start = block_start
                current_tokens = 0
            if not current:
                current_start = block_start
            current.extend(block_lines)
            current_tokens += block_tokens

        if current:
            chunks.append(
                self._make_code_chunk(
                    source=source,
                    file_path=file_path,
                    language=language,
                    content="\n".join(current),
                    locator=self._locator_for(
                        source, file_path, current_start, current_start + len(current) - 1
                    ),
                    prefix_lines=current_start,
                    symbols=symbols,
                )
            )
        return chunks or [
            self._make_code_chunk(
                source=source,
                file_path=file_path,
                language=language,
                content=text,
                locator=self._whole_locator(source, file_path),
                prefix_lines=0,
                symbols=symbols,
            )
        ]

    # --- per-unit path (registry, fenced code inside a markdown source) -----

    def chunk(self, unit: ContentUnit, source: NormalizedSource) -> List[ChunkCandidate]:
        code = unit.text if unit.text else (unit.markdown or "")
        if not code.strip():
            return []
        heading_path = list(unit.hierarchy.heading_path) if unit.hierarchy else []
        locator = unit.locator.to_dict() if unit.locator else {}
        language = unit.metadata.get("language") or source.language or source.metadata.get(
            "language"
        )
        if self.token_counter.count(code) <= self.max_tokens:
            return [self._make_chunk(source, unit, code, language, heading_path, locator)]

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
                        source, unit, "\n".join(current), language, heading_path, locator
                    )
                )
                current = []
                current_tokens = 0
            current.extend(block)
            current_tokens += block_tokens
        if current:
            chunks.append(
                self._make_chunk(
                    source, unit, "\n".join(current), language, heading_path, locator
                )
            )
        return chunks or [
            self._make_chunk(source, unit, code, language, heading_path, locator)
        ]

    @staticmethod
    def _split_blocks(lines: List[str]) -> List[List[str]]:
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

    @staticmethod
    def _line_blocks(lines: List[str]) -> List[tuple]:
        """Returns ``[(start_index, block_lines)]`` groups separated by blanks."""
        blocks: List[tuple] = []
        start: Optional[int] = None
        current: List[str] = []
        for i, line in enumerate(lines):
            if line.strip():
                if start is None:
                    start = i
                current.append(line)
            elif current:
                blocks.append((start, current))
                current = []
                start = None
        if current:
            blocks.append((start, current))
        return blocks

    # --- helpers -----------------------------------------------------------

    def _make_code_chunk(
        self,
        *,
        source: NormalizedSource,
        file_path: Optional[str],
        language: Optional[str],
        content: str,
        locator: dict,
        prefix_lines: int,
        symbols: List[dict],
    ) -> ChunkCandidate:
        symbol = self._containing_symbol(symbols, locator.get("line_start"), locator.get("line_end"))
        header = self._generic_header(
            file_path=file_path,
            language=language,
            symbol_name=None if symbol is None else symbol["name"],
            symbol_type=None if symbol is None else symbol["type"],
            signature=None if symbol is None else symbol.get("signature"),
        )
        embedding_text = f"{header}\n\n{content}" if content else header
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ChunkCandidate(
            chunk_id=f"{source.source_id}:code:{uuid.uuid4().hex[:12]}",
            source_id=source.source_id,
            version_id=source.version_id,
            chunk_type="code",
            content=content,
            embedding_text=embedding_text,
            heading_path=[key for key in (file_path,) if key],
            locator=locator,
            content_hash=content_hash,
            metadata={
                "chunker_profile": self.profile.to_dict(),
                "language": language,
                "symbol_name": None if symbol is None else symbol["name"],
                "symbol_type": None if symbol is None else symbol["type"],
                "signature": None if symbol is None else symbol.get("signature"),
            },
            token_count=self.token_counter.count(content),
        )

    @staticmethod
    def _containing_symbol(symbols: List[dict], line_start, line_end) -> Optional[dict]:
        ls = line_start
        le = line_end
        for s in symbols:
            s0, s1 = s.get("start"), s.get("end")
            if s0 is None or s1 is None:
                continue
            # overlap between chunk range and symbol range
            if ls is not None and le is not None:
                if s0 <= le and s1 >= ls:
                    return s
            elif ls is not None and s0 <= ls <= s1:
                return s
        return None

    @staticmethod
    def _whole_locator(source: NormalizedSource, file_path: Optional[str]) -> dict:
        code_units = [
            u for u in source.units if u.unit_type is UnitType.CODE and u.locator
        ]
        if code_units:
            loc = code_units[0].locator
            return {
                "file_path": loc.file_path,
                "line_start": loc.line_start,
                "line_end": loc.line_end,
                "symbol_name": None,
                "symbol_type": None,
                "block_index": loc.block_index,
            }
        return {"file_path": file_path, "line_start": None, "line_end": None}

    @staticmethod
    def _locator_for(source, file_path, start_idx: int, end_idx: int) -> dict:
        return {
            "file_path": file_path,
            "line_start": start_idx + 1,
            "line_end": end_idx + 1,
            "symbol_name": None,
            "symbol_type": None,
            "block_index": start_idx + 1,
        }

    @staticmethod
    def _generic_header(
        *,
        file_path: Optional[str],
        language: Optional[str],
        symbol_name: Optional[str],
        symbol_type: Optional[str],
        signature: Optional[str],
    ) -> str:
        parts = [
            f"File: {file_path or ''}",
            f"Language: {language or ''}",
        ]
        if symbol_name:
            parts.append(f"Symbol: {symbol_name}")
            if symbol_type:
                parts.append(f"Type: {symbol_type}")
        if signature:
            parts.append(f"Signature: {signature}")
        return "# " + " | ".join(parts)

    def _make_chunk(
        self,
        source: NormalizedSource,
        unit: ContentUnit,
        content: str,
        language: Optional[str],
        heading_path: List[str],
        locator: dict,
    ) -> ChunkCandidate:
        symbol = self._containing_symbol_for_unit(unit, source)
        header = self._generic_header(
            file_path=locator.get("file_path") or unit.metadata.get("file_path"),
            language=language or unit.metadata.get("language"),
            symbol_name=None if symbol is None else symbol["name"],
            symbol_type=None if symbol is None else symbol["type"],
            signature=None if symbol is None else symbol.get("signature"),
        )
        base_embedding = build_embedding_text(heading_path, content)
        if header and header not in base_embedding:
            embedding_text = f"{header}\n\n{base_embedding}"
        else:
            embedding_text = base_embedding
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

    @staticmethod
    def _containing_symbol_for_unit(unit: ContentUnit, source: NormalizedSource):
        if not unit.locator:
            return None
        ls, le = unit.locator.line_start, unit.locator.line_end
        symbols: List[dict] = []
        for u in source.units:
            if u.unit_type is UnitType.SYMBOL and u.locator:
                symbols.append(
                    {
                        "name": u.locator.symbol_name,
                        "type": u.locator.symbol_type,
                        "start": u.locator.line_start,
                        "end": u.locator.line_end,
                        "signature": u.metadata.get("signature"),
                    }
                )
        return CodeChunker._containing_symbol(symbols, ls, le)
