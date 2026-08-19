"""Dedicated PL/SQL chunker (Aşama 7.5).

PL/SQL needs symbol-aware chunking that a generic blank-line splitter cannot
provide:

* Recognise ``PACKAGE`` / ``PACKAGE BODY`` / ``PROCEDURE`` / ``FUNCTION`` /
  ``TRIGGER`` / ``TYPE`` boundaries.
* Never split *inside* a string or a ``--`` / ``/* */`` comment — keywords such
  as ``PROCEDURE`` that appear in a string literal or a comment must not be
  mistaken for a declaration boundary. This is guaranteed by first producing a
  "clean" view of the file where string bodies and comment text are removed
  while tracking quote/comment state across lines, and then running every
  boundary regex against that clean view.
* Attach the signature (declaration header) and the enclosing-package name to
  every chunk produced from a package body.
* If a single oversized procedure exceeds ``CHUNK_MAX_TOKENS``, split it only
  at *inner block* boundaries (``BEGIN`` / ``IF`` / ``LOOP`` / ``FOR`` /
  ``END ...`` / blank lines) and re-add the signature + enclosing-symbol
  context to each inner chunk, linking them to a parent chunk that holds the
  full symbol.

Each ``ChunkCandidate`` carries:
``chunk_type``, ``content``, ``embedding_text`` (with a header of file path +
language + symbol + signature), ``heading_path``, ``sequence_no`` (assigned by
the registry), ``parent_chunk_id``, ``content_hash``, an explicit ``locator``
(symbol name + line range) and ``chunker_profile`` metadata. The ``TokenCounter``
receiver is injected (never imported) so the concrete ``token_counter`` module
stays decoupled.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, Dict, List, Optional

from ...config import settings
from ...domain.normalized_content import ContentUnit, NormalizedSource, UnitType
from .base import ChunkCandidate, ChunkerProfile, NaiveTokenCounter

__all__ = ["PlSqlChunker", "strip_plsql_lines"]

PLSQL_LANGS = frozenset({"plsql", "sql"})

# PL/SQL object-header forms that open a *top-level* standalone object.
_CREATE_OBJ_RE = re.compile(
    r"^CREATE\s+(OR\s+REPLACE\s+)?"
    r"(?P<kind>PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE\s+BODY|TYPE)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_$#]*)",
    re.IGNORECASE,
)

# Subprogram declarations inside a PACKAGE BODY (bodies: "... IS ... BEGIN ...").
_SUBPROGRAM_RE = re.compile(
    r"^\s*(PROCEDURE|FUNCTION)\s+(?P<name>[A-Za-z_][A-Za-z0-9_$#]*)", re.IGNORECASE
)

# Body-opening keyword that terminates the declaration/signature header.
_IS_AS_RE = re.compile(r"\b(IS|AS)\b", re.IGNORECASE)

# Inner block boundaries: splitting a huge procedure is only allowed at these.
_BLOCK_START_RE = re.compile(
    r"^(BEGIN|LOOP|IF|FOR|WHILE|CASE|EXCEPTION|DECLARE|ELSIF|ELSE|WHEN|END)\b",
    re.IGNORECASE,
)


def strip_plsql_lines(text: str) -> List[str]:
    """Returns one "clean code" line per source line.

    String literals (``'...'`` with ``''`` escapes and ``q'...'`` quoting) and
    comment text (``--`` line comments, ``/* */`` block comments) are removed
    while quote/comment state is tracked across lines, so keywords in strings
    or comments can never cause a false boundary split.
    """
    lines = text.splitlines()
    cleaned: List[str] = []
    in_block = False
    in_string = False
    q_delim: Optional[str] = None
    _pairs = {"[": "]", "{": "}", "(": ")", "<": ">"}

    for line in lines:
        out: List[str] = []
        i = 0
        n = len(line)
        while i < n:
            c = line[i]
            nxt = line[i + 1] if i + 1 < n else ""

            if in_block:
                if c == "*" and nxt == "/":
                    in_block = False
                    i += 2
                else:
                    i += 1
                continue

            if q_delim is not None:
                if c == q_delim and nxt == "'":
                    q_delim = None
                    i += 2
                else:
                    i += 1
                continue

            if in_string:
                if c == "'":
                    if nxt == "'":
                        i += 2
                        continue
                    in_string = False
                    i += 1
                    continue
                i += 1
                continue

            # --- code position ---
            if c == "'":
                in_string = True
                i += 1
                continue

            if (c == "q" or c == "Q") and nxt == "'":
                j = i + 2
                if j < n:
                    d = line[j]
                    close_d = _pairs.get(d, d)
                    k = j + 1
                    found = False
                    while k + 1 < n:
                        if line[k] == close_d and line[k + 1] == "'":
                            found = True
                            break
                        k += 1
                    if found:
                        i = k + 2
                        continue
                    q_delim = close_d
                    i = n
                    continue
                in_string = True
                i += 1
                continue

            if c == "-" and nxt == "-":
                break
            if c == "/" and nxt == "*":
                in_block = True
                i += 2
                continue

            out.append(c)
            i += 1

        cleaned.append("".join(out).strip())
    return cleaned


def _is_split_point(cleaned_line: str) -> bool:
    """True if a line is an allowed inner-block split point."""
    s = (cleaned_line or "").strip()
    if not s:
        return True
    return bool(_BLOCK_START_RE.match(s))


def _header_signature(raw_lines: List[str], start: int, end: int) -> str:
    """Signature = declaration header: from ``start`` through the ``IS/AS``
    line (or just the first line when the body opener is absent)."""
    for i in range(start, min(end, len(raw_lines) - 1) + 1):
        if _IS_AS_RE.search(raw_lines[i]):
            return " ".join(x.strip() for x in raw_lines[start : i + 1])
    return (raw_lines[start] if raw_lines and start < len(raw_lines) else "").strip()


class PlSqlChunker:
    """Splits a PL/SQL source into symbol-aware ``ChunkCandidate`` chunks.

    ``counter`` is any ``TokenCounter`` receiver (``count(text) -> int``);
    when ``None`` a conservative ``len//4`` fallback is used.
    """

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

    # --- public entry points ----------------------------------------------

    def chunk(self, unit: ContentUnit, source: NormalizedSource) -> List[ChunkCandidate]:
        """Registry-compatible: chunk a single CODE unit with source context."""
        return self.chunk_source(source)

    def chunk_source(self, source: NormalizedSource) -> List[ChunkCandidate]:
        """Chunks the whole source (its CODE units) into symbol-aware pieces."""
        code_units = [
            u for u in source.units if u.unit_type is UnitType.CODE and u.text.strip()
        ]
        if not code_units:
            return []
        text = "\n".join(u.text for u in code_units)
        file_path = (
            source.metadata.get("file_path")
            or (code_units[0].locator.file_path if code_units[0].locator else None)
            or source.title
        )
        language = source.language or source.metadata.get("language") or "plsql"
        chunks = self._chunk_plsql(text, source=source, file_path=file_path, language=language)
        chunks.sort(key=lambda c: (c.locator.get("line_start", 0) or 0))
        return chunks

    # --- scanning + building ---------------------------------------------

    def _chunk_plsql(
        self, text: str, *, source: NormalizedSource, file_path: str, language: str
    ) -> List[ChunkCandidate]:
        raw_lines = text.splitlines() or [""]
        cleaned = strip_plsql_lines(text)
        n = len(raw_lines)

        top: List[Dict[str, Any]] = []
        for idx, cl in enumerate(cleaned):
            m = _CREATE_OBJ_RE.match(cl)
            if m:
                kind_raw = re.sub(r"\s+", " ", m.group("kind").strip().upper())
                top.append(
                    {
                        "index": idx,
                        "kind": kind_raw,
                        "name": re.split(r"\s+", m.group("name"))[0].upper(),
                    }
                )

        segments: List[Dict[str, Any]] = []
        last = 0
        for pos, obj in enumerate(top):
            s = obj["index"]
            if s > last:
                segments.append(self._generic(last, s - 1))
            nxt = top[pos + 1]["index"] - 1 if pos + 1 < len(top) else n - 1
            segments.extend(self._expand_object(raw_lines, cleaned, obj, nxt))
            last = nxt + 1
        if last < n:
            segments.append(self._generic(last, n - 1))

        return self._build_chunks(segments, raw_lines, cleaned, source, file_path, language)

    @staticmethod
    def _generic(start: int, end: int) -> Dict[str, Any]:
        return {
            "start": start,
            "end": end,
            "kind": "section",
            "name": None,
            "signature": None,
            "enclosing": None,
        }

    def _expand_object(
        self, raw_lines: List[str], cleaned: List[str], obj: Dict[str, Any], end: int
    ) -> List[Dict[str, Any]]:
        start = obj["index"]
        kind = obj["kind"]
        name = obj["name"]

        if kind == "PACKAGE":
            return [
                {
                    "start": start,
                    "end": end,
                    "kind": "package",
                    "name": name,
                    "signature": _header_signature(raw_lines, start, end),
                    "enclosing": None,
                }
            ]

        if kind == "PACKAGE BODY":
            subs: List[int] = []
            for i in range(start + 1, end + 1):
                if _SUBPROGRAM_RE.match(cleaned[i]):
                    subs.append(i)
            segs: List[Dict[str, Any]] = []
            if not subs:
                segs.append(
                    {
                        "start": start,
                        "end": end,
                        "kind": "package body",
                        "name": name,
                        "signature": _header_signature(raw_lines, start, end),
                        "enclosing": None,
                    }
                )
                return segs

            # package header (CREATE ... IS through declarations)
            segs.append(
                {
                    "start": start,
                    "end": subs[0] - 1,
                    "kind": "package body",
                    "name": name,
                    "signature": _header_signature(raw_lines, start, subs[0] - 1),
                    "enclosing": None,
                }
            )
            for pos, sub_start in enumerate(subs):
                sub_end = subs[pos + 1] - 1 if pos + 1 < len(subs) else end
                m = _SUBPROGRAM_RE.match(cleaned[sub_start])
                sub_kind = m.group(1).upper() if m else "SUBROGRAM"
                segs.append(
                    {
                        "start": sub_start,
                        "end": sub_end,
                        "kind": sub_kind,
                        "name": m.group("name").upper() if m else None,
                        "signature": _header_signature(raw_lines, sub_start, sub_end),
                        "enclosing": name,
                    }
                )
            return segs

        # standalone PROCEDURE / FUNCTION / TRIGGER / TYPE / TYPE BODY
        return [
            {
                "start": start,
                "end": end,
                "kind": kind,
                "name": name,
                "signature": _header_signature(raw_lines, start, end),
                "enclosing": None,
            }
        ]

    def _build_chunks(
        self,
        segments: List[Dict[str, Any]],
        raw_lines: List[str],
        cleaned: List[str],
        source: NormalizedSource,
        file_path: str,
        language: str,
    ) -> List[ChunkCandidate]:
        chunks: List[ChunkCandidate] = []
        block_index = 0
        for seg in segments:
            start, end = seg["start"], seg["end"]
            if start < 0 or end < start:
                continue
            content = "\n".join(raw_lines[start : end + 1])
            if not content.strip():
                continue
            block_index += 1
            loc = {
                "file_path": file_path,
                "line_start": start + 1,
                "line_end": end + 1,
                "symbol_name": seg["name"],
                "symbol_type": (seg["kind"] or "code").upper(),
                "block_index": block_index,
            }

            if self.token_counter.count(content) <= self.max_tokens:
                chunks.append(
                    self._make_chunk(
                        source=source,
                        file_path=file_path,
                        language=language,
                        content=content,
                        loc=loc,
                        seg=seg,
                        block_index=block_index,
                        parent_id=None,
                    )
                )
            else:
                chunks.extend(
                    self._split_oversized(
                        source=source,
                        file_path=file_path,
                        language=language,
                        raw_lines=raw_lines,
                        cleaned=cleaned,
                        start=start,
                        end=end,
                        loc=loc,
                        seg=seg,
                    )
                )
        return chunks

    def _split_oversized(
        self,
        *,
        source: NormalizedSource,
        file_path: str,
        language: str,
        raw_lines: List[str],
        cleaned: List[str],
        start: int,
        end: int,
        loc: Dict[str, Any],
        seg: Dict[str, Any],
    ) -> List[ChunkCandidate]:
        """Splits an oversized symbol at inner block boundaries only, and
        re-adds signature + enclosing-symbol context to every inner chunk,
        linking them to a parent chunk holding the full symbol."""
        content = "\n".join(raw_lines[start : end + 1])
        parent = self._make_chunk(
            source=source,
            file_path=file_path,
            language=language,
            content=content,
            loc=dict(loc),
            seg=seg,
            block_index=loc["block_index"],
            parent_id=None,
            chunk_kind="symbol_parent",
        )

        # Boundaries at which a new inner piece may begin: the symbol start,
        # every inner-block boundary line, and the end of the symbol.
        bounds = [start]
        bounds.extend(
            i for i in range(start + 1, end + 1) if _is_split_point(cleaned[i])
        )
        bounds.append(end + 1)

        # Group lines into blocks each starting at a boundary, then combine
        # consecutive blocks greedily so each piece stays within budget while
        # only ever cutting at an inner-block boundary.
        blocks: List[tuple] = []
        for pos in range(len(bounds) - 1):
            bs, be = bounds[pos], bounds[pos + 1] - 1
            if be >= bs:
                blocks.append((bs, be))

        pieces: List[tuple] = []
        current = []
        current_tokens = 0
        for bs, be in blocks:
            block_tokens = self.token_counter.count("\n".join(raw_lines[bs : be + 1]))
            if current and current_tokens + block_tokens > self.max_tokens:
                pieces.append((current[0][0], current[-1][1]))
                current = []
                current_tokens = 0
            current.append((bs, be))
            current_tokens += block_tokens
        if current:
            pieces.append((current[0][0], current[-1][1]))
        if not pieces:
            pieces = [(start, end)]

        children: List[ChunkCandidate] = []
        block_index = loc["block_index"]
        for s, e in pieces:
            block_index += 1
            part = "\n".join(raw_lines[s : e + 1])
            children.append(
                self._make_chunk(
                    source=source,
                    file_path=file_path,
                    language=language,
                    content=part,
                    loc={
                        "file_path": file_path,
                        "line_start": s + 1,
                        "line_end": e + 1,
                        "symbol_name": loc["symbol_name"],
                        "symbol_type": loc["symbol_type"],
                        "block_index": block_index,
                    },
                    seg=seg,
                    block_index=block_index,
                    parent_id=parent.chunk_id,
                    chunk_kind="inner_split",
                )
            )

        return [parent] + children

    def _make_chunk(
        self,
        *,
        source: NormalizedSource,
        file_path: str,
        language: str,
        content: str,
        loc: Dict[str, Any],
        seg: Dict[str, Any],
        block_index: int,
        parent_id: Optional[str],
        chunk_kind: Optional[str] = None,
    ) -> ChunkCandidate:
        symbol_name = seg["name"]
        symbol_type = (seg["kind"] or "code").upper()
        enclosing = seg["enclosing"]
        signature = seg["signature"]

        heading_path: List[str] = []
        if symbol_name:
            heading_path = [symbol_name]
            if enclosing:
                heading_path = [enclosing, symbol_name]

        header = self._embedding_header(
            file_path=file_path,
            language=language,
            symbol_type=symbol_type,
            symbol_name=symbol_name,
            signature=signature,
            enclosing=enclosing,
        )
        embedding_text = f"{header}\n\n{content}" if content else header
        metadata = {
            "chunker_profile": self.profile.to_dict(),
            "language": language,
            "chunk_kind": chunk_kind or symbol_type,
            "symbol_name": symbol_name,
            "symbol_type": symbol_type,
            "signature": signature,
            "enclosing_package": enclosing,
        }
        return ChunkCandidate(
            chunk_id=f"{source.source_id}:code:{uuid.uuid4().hex[:12]}",
            source_id=source.source_id,
            version_id=source.version_id,
            chunk_type="code",
            content=content,
            embedding_text=embedding_text,
            heading_path=heading_path,
            locator=loc,
            parent_chunk_id=parent_id,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            metadata=metadata,
            token_count=self.token_counter.count(content),
        )

    @staticmethod
    def _embedding_header(
        *,
        file_path: str,
        language: str,
        symbol_type: str,
        symbol_name: Optional[str],
        signature: Optional[str],
        enclosing: Optional[str],
    ) -> str:
        parts = [
            f"File: {file_path or ''}",
            f"Language: {language or ''}",
            f"Symbol: {symbol_name or ''}",
            f"Type: {symbol_type or ''}",
        ]
        if enclosing:
            parts.append(f"Enclosing: {enclosing}")
        if signature:
            parts.append(f"Signature: {signature}")
        return "# " + " | ".join(parts)

