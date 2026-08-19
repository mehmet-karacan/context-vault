"""Parser Router (Aşama 3.1).

Selects a ``DomainParser``-conforming parser for a raw source file based on
MIME type, file extension and (where cheaply available) magic bytes, applies
a per-file timeout and a maximum output-size limit, and returns the parse as
a shared ``NormalizedSource`` (Bölüm 6).

Selection / trust rules (AKTIF_GOREV.md 3.1 — "Extension ile MIME çelişirse
dosyayı otomatik güvenilir sayma"):

- Magic bytes are ground truth for the *actual* file type. When they clearly
  identify a known type, that type wins even if the extension or a
  client-supplied MIME disagrees — a file is never trusted blindly by its
  name/label.
- If magic is inconclusive (e.g. plain text or an unknown binary) and both
  an extension-derived type and a MIME-derived type are present *and they
  conflict*, the file is not auto-trusted: an ``AmbiguousSourceTypeError`` is
  raised instead of guessing.
- Otherwise the single available signal (MIME-derived preferred over
  extension-derived) decides; an unrecognised type raises
  ``UnsupportedFileTypeError``.

The dedicated parsers (``docx_parser``, ``pdf_parser``/``docling_parser``,
``plain_text_parser``, ``image_parser``, ``code_parser``) do not exist yet —
this stage only lays the foundational router. The registry maps each
source_type to a parser instance conforming to ``DocumentParser``; concrete
adapters can be swapped in later without touching the router's contract.
``PlainTextParser`` / ``MarkdownParser`` are genuinely implemented here so
the pipeline is end-to-end functional; the rest are minimal placeholders.
"""

from __future__ import annotations

import codecs
import concurrent.futures
import mimetypes
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from ...config import settings
from .docx_parser import DocxParser
from .pdf_parser import PdfParser
from ...domain.normalized_content import (
    ContentUnit,
    Hierarchy,
    NormalizedSource,
    SourceLocator,
    UnitType,
)
from ...domain.ports import DocumentParser


class ParserError(Exception):
    """Base class for all ParserRouter failures."""


class UnsupportedFileTypeError(ParserError, NotImplementedError):
    """Raised when no parser can handle the detected source type."""


class AmbiguousSourceTypeError(ParserError):
    """Raised when signals conflict and the actual type cannot be trusted."""


class ParserTimeoutError(ParserError):
    """Raised when a parser exceeds the configured timeout."""


class ParserOutputLimitError(ParserError):
    """Raised when a parser's output exceeds the configured max char count."""


# --- Signal classification tables -----------------------------------------

_EXTENSION_TYPES: Dict[str, str] = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".txt": "plain_text",
    ".text": "plain_text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".bmp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".webp": "image",
}

# First / shortest MIME token is used as the authoritative source signal.
_MIME_TYPES: Dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "plain_text",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
}

_IMAGE_MIME_PREFIXES = ("image/",)


def _classify_magic(magic: bytes) -> Optional[str]:
    """Returns a source_type for well-known magic bytes, else ``None``."""
    if not magic:
        return None
    if magic.startswith(b"%PDF-"):
        return "pdf"
    if magic.startswith(b"\x89PNG"):
        return "image"
    if magic.startswith(b"\xff\xd8\xff"):
        return "image"
    if magic.startswith(b"GIF8"):
        return "image"
    if magic.startswith(b"II*\x00") or magic.startswith(b"MM\x00*"):
        return "image"
    if magic.startswith(b"PK\x03\x04"):
        # ZIP container — almost certainly a DOCX (or XLSX/PPTX). Only treat
        # it as DOCX when the extension/MIME already suggests office documents,
        # so we don't misroute other ZIP payloads.
        return "zip"
    return None


def _mime_type_from_mime(mime_type: Optional[str]) -> Optional[str]:
    if not mime_type:
        return None
    mime = mime_type.split(";", 1)[0].strip().lower()
    if mime in _MIME_TYPES:
        return _MIME_TYPES[mime]
    if mime.startswith(_IMAGE_MIME_PREFIXES):
        return "image"
    return None


def _extension_from_filename(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    return os.path.splitext(os.path.basename(filename))[1].lower()


def _type_from_extension(filename: Optional[str]) -> Optional[str]:
    return _EXTENSION_TYPES.get(_extension_from_filename(filename) or "")


# --- Concrete parser implementations --------------------------------------

def _base_source(source_type: str, title: Optional[str]) -> NormalizedSource:
    return NormalizedSource(
        source_id=str(uuid.uuid4()),
        source_type=source_type,
        title=title,
    )


def _trim_heading_stack(stack: List[str], depth: int) -> None:
    """Pops entries so the stack reflects the current heading level."""
    while len(stack) >= depth:
        stack.pop()


def _mk_unit(
    *,
    unit_type: UnitType,
    unit_id: str,
    text: str,
    markdown: Optional[str],
    order: int,
    filename: str,
    line_start: int,
    line_end: int,
    block_index: int,
    heading_path: List[str],
    depth: Optional[int] = None,
    parent_unit_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ContentUnit:
    """Builds a ContentUnit carrying order, hierarchy, locator and metadata."""
    return ContentUnit(
        unit_id=unit_id,
        unit_type=unit_type,
        text=text,
        markdown=markdown,
        order=order,
        hierarchy=Hierarchy(
            heading_path=list(heading_path),
            parent_unit_id=parent_unit_id,
            depth=depth if depth is not None else len(heading_path),
        ),
        locator=SourceLocator(
            file_path=filename,
            line_start=line_start,
            line_end=line_end,
            block_index=block_index,
        ),
        metadata=dict(metadata or {}),
    )


# --- Encoding detection (TXT / Markdown, Aşama 3.4) ------------------------
#
# No external charset-detection library is guaranteed to be installed in the
# offline service environment, so decoding uses a controlled, deterministic
# heuristic:
#   1. Honour an explicit byte-order mark (UTF-8/16/32) when present.
#   2. Try a small curated list of encodings in *strict* mode.
#   3. Fall back to UTF-8 with ``errors="replace"`` so parsing always succeeds.
# The detected encoding + any BOM/fallback signal is recorded in metadata.


_ENCODING_CANDIDATES: Tuple[str, ...] = ("utf-8", "cp1254", "cp1252", "latin-1")


def _decode_bytes(raw: bytes) -> Tuple[str, str, Dict[str, Any]]:
    """Returns ``(text, encoding_name, info)`` for raw file bytes."""
    info: Dict[str, Any] = {}
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig", {"bom": "utf-8"}
    if raw.startswith(codecs.BOM_UTF32_LE):
        return (
            raw.decode("utf-32-le").lstrip("\ufeff"),
            "utf-32-le",
            {"bom": "utf-32-le"},
        )
    if raw.startswith(codecs.BOM_UTF32_BE):
        return (
            raw.decode("utf-32-be").lstrip("\ufeff"),
            "utf-32-be",
            {"bom": "utf-32-be"},
        )
    if raw.startswith(codecs.BOM_UTF16_LE):
        return (
            raw.decode("utf-16-le").lstrip("\ufeff"),
            "utf-16-le",
            {"bom": "utf-16-le"},
        )
    if raw.startswith(codecs.BOM_UTF16_BE):
        return (
            raw.decode("utf-16-be").lstrip("\ufeff"),
            "utf-16-be",
            {"bom": "utf-16-be"},
        )

    for candidate in _ENCODING_CANDIDATES:
        try:
            text = raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
        if candidate == "utf-8":
            # A strict UTF-8 decode is the strongest available signal; do not
            # fall through to a lossy single-byte candidate that would also
            # "succeed" for every byte sequence.
            return text, "utf-8", info
        return text, candidate, info

    return raw.decode("utf-8", errors="replace"), "utf-8", {
        **info,
        "fallback": "replace",
    }


def _detect_language(text: str) -> Optional[str]:
    """Best-effort language hint from the decoded text (lightweight only).

    Detects nothing by itself right now; the caller may pass an explicit
    ``options["language"]`` which is preferred over this best-effort hint.
    """
    return None


class PlainTextParser:
    """Aşama 3.4: plain-text reader with encoding detection + line info.

    Consecutive non-blank lines are grouped into paragraph blocks. Each block
    becomes one ``paragraph`` ``ContentUnit`` carrying an accurate
    ``line_start``/``line_end`` locator (3.4: "Büyük düz metin dosyalarında
    satır bilgisi üret"), so even very large files can be cited by line range.
    """

    source_type = "plain_text"

    def supports(self, mime_type: str, extension: str) -> bool:
        ext = (extension or "").lower().lstrip(".")
        mime = (mime_type or "").split(";", 1)[0].strip().lower()
        return ext in ("txt", "text") or mime in ("text/plain", "text")

    def parse(
        self, file_path: str, filename: str, options: Optional[dict] = None
    ) -> NormalizedSource:
        options = options or {}
        with open(file_path, "rb") as fh:
            raw = fh.read()
        text, encoding, enc_info = _decode_bytes(raw)

        source = _base_source(self.source_type, filename)
        source.metadata["encoding"] = encoding
        source.metadata.update(enc_info)
        source.metadata["line_count"] = len(text.splitlines())
        if options.get("language"):
            source.language = str(options["language"])

        lines = text.splitlines()
        units: List[ContentUnit] = []
        order = 0
        block_index = 0
        block_start: Optional[int] = None
        block_lines: List[str] = []

        def flush_block(end_line: int) -> None:
            nonlocal order, block_index, block_start, block_lines
            if not block_lines:
                return
            order += 1
            block_index += 1
            block_text = "\n".join(block_lines).strip()
            units.append(
                _mk_unit(
                    unit_type=UnitType.PARAGRAPH,
                    unit_id=f"{self.source_type}:{block_index}",
                    text=block_text,
                    markdown=block_text,
                    order=order,
                    filename=filename,
                    line_start=block_start or end_line,
                    line_end=end_line,
                    block_index=block_index,
                    heading_path=[],
                    metadata={"encoding": encoding},
                )
            )
            block_lines = []
            block_start = None

        for i, line in enumerate(lines, start=1):
            if line.strip():
                if block_start is None:
                    block_start = i
                block_lines.append(line)
            else:
                flush_block(i - 1)
        flush_block(len(lines))

        source.units = units
        return source


# --- Markdown block classification helpers --------------------------------


def _atx_heading(stripped: str) -> Optional[Tuple[int, str]]:
    """Returns ``(depth, text)`` for an ATX heading line, else ``None``."""
    if not stripped.startswith("#"):
        return None
    hashes = 0
    while hashes < len(stripped) and stripped[hashes] == "#":
        hashes += 1
    if hashes > 6:
        return None
    text = stripped[hashes:].strip()
    if not text:
        return None
    return hashes, text


def _fence_info(stripped: str) -> Optional[Tuple[str, Optional[str]]]:
    """Returns ``(marker, language)`` for a code-fence line, else ``None``."""
    marker = None
    if stripped.startswith("```"):
        marker = "```"
    elif stripped.startswith("~~~"):
        marker = "~~~"
    if marker is None:
        return None
    rest = stripped[len(marker):].strip()
    return marker, (rest or None)


def _is_fence_close(line: str, marker: str) -> bool:
    stripped = line.strip()
    return stripped == marker


_LIST_ITEM_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")

# A markdown table separator cell: `---`, `:---`, `---:`, `:---:`.
_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def _is_list_item(stripped: str) -> bool:
    if not stripped:
        return False
    return bool(_LIST_ITEM_RE.match(stripped))


def _is_table_separator(stripped: str) -> bool:
    if not stripped or "-" not in stripped:
        return False
    body = stripped.strip().strip("|")
    if not body:
        return False
    cells = body.split("|") if "|" in body else [body]
    has_sep = False
    for cell in cells:
        c = cell.strip()
        if not c:
            continue
        if not _TABLE_SEP_CELL_RE.match(c):
            return False
        has_sep = True
    return has_sep


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|")


class MarkdownParser:
    """Aşama 3.4: a light Markdown reader preserving document structure.

    Emits ``heading`` (with a heading_path hierarchy rebuilt from ``#``-levels),
    ``code`` (fenced blocks, one unit per fence with the raw fence preserved),
    ``list_item`` (one unit per bullet/numbered item), ``table`` (one unit per
    pipe table, rendering preserved) and ``paragraph`` units. Every unit keeps
    its heading_path, order, locator (line_start/line_end) and metadata.
    """

    source_type = "markdown"

    def supports(self, mime_type: str, extension: str) -> bool:
        ext = (extension or "").lower().lstrip(".")
        mime = (mime_type or "").split(";", 1)[0].strip().lower()
        return ext in ("md", "markdown") or mime in (
            "text/markdown",
            "text/x-markdown",
        )

    def parse(
        self, file_path: str, filename: str, options: Optional[dict] = None
    ) -> NormalizedSource:
        options = options or {}
        with open(file_path, "rb") as fh:
            raw = fh.read()
        text, encoding, enc_info = _decode_bytes(raw)

        source = _base_source(self.source_type, filename)
        source.metadata["encoding"] = encoding
        source.metadata.update(enc_info)
        source.metadata["line_count"] = len(text.splitlines())
        if options.get("language"):
            source.language = str(options["language"])

        lines = text.splitlines()
        units: List[ContentUnit] = []
        order = 0
        block_index = 0
        heading_stack: List[str] = []
        last_heading_id: Optional[str] = None
        i = 0
        n = len(lines)

        def next_unit() -> int:
            nonlocal order, block_index
            order += 1
            block_index += 1
            return block_index

        while i < n:
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                continue

            # ---- heading ----
            heading = _atx_heading(stripped)
            if heading:
                depth, heading_text = heading
                _trim_heading_stack(heading_stack, depth)
                heading_stack.append(heading_text)
                bi = next_unit()
                unit = _mk_unit(
                    unit_type=UnitType.HEADING,
                    unit_id=f"{self.source_type}:{bi}",
                    text=heading_text,
                    markdown=stripped,
                    order=order,
                    filename=filename,
                    line_start=i + 1,
                    line_end=i + 1,
                    block_index=bi,
                    heading_path=list(heading_stack),
                    depth=depth,
                    metadata={"heading_level": depth},
                )
                last_heading_id = unit.unit_id
                units.append(unit)
                i += 1
                continue

            # ---- fenced code block ----
            fence = _fence_info(stripped)
            if fence:
                marker, lang = fence
                start = i + 1
                j = i + 1
                code_lines: List[str] = []
                while j < n:
                    if _is_fence_close(lines[j], marker):
                        break
                    code_lines.append(lines[j])
                    j += 1
                end = j + 1  # include the closing fence line
                bi = next_unit()
                metadata: Dict[str, Any] = {}
                if lang:
                    metadata["language"] = lang
                units.append(
                    _mk_unit(
                        unit_type=UnitType.CODE,
                        unit_id=f"{self.source_type}:{bi}",
                        text="\n".join(code_lines),
                        markdown="\n".join(lines[i:end]),
                        order=order,
                        filename=filename,
                        line_start=start,
                        line_end=end,
                        block_index=bi,
                        heading_path=list(heading_stack),
                        parent_unit_id=last_heading_id,
                        metadata=metadata,
                    )
                )
                i = end
                continue

            # ---- pipe table ----
            if (
                _is_table_row(lines[i])
                and i + 1 < n
                and _is_table_separator(lines[i + 1].strip())
            ):
                start = i + 1
                j = i + 1
                while j < n and lines[j].strip() and not _atx_heading(lines[j].strip()):
                    if _fence_info(lines[j].strip()):
                        break
                    j += 1
                end = j
                bi = next_unit()
                rows = lines[i:end]
                units.append(
                    _mk_unit(
                        unit_type=UnitType.TABLE,
                        unit_id=f"{self.source_type}:{bi}",
                        text="\n".join(row.strip() for row in rows),
                        markdown="\n".join(rows),
                        order=order,
                        filename=filename,
                        line_start=start,
                        line_end=end,
                        block_index=bi,
                        heading_path=list(heading_stack),
                        parent_unit_id=last_heading_id,
                        metadata={"row_count": len(rows)},
                    )
                )
                i = end
                continue

            # ---- list items ----
            if _is_list_item(stripped):
                j = i
                item_lines: List[int] = []
                while j < n and _is_list_item(lines[j].strip()):
                    if lines[j].strip():
                        item_lines.append(j)
                    j += 1
                end = j
                for k in item_lines:
                    ls = k + 1
                    bi = next_unit()
                    units.append(
                        _mk_unit(
                            unit_type=UnitType.LIST_ITEM,
                            unit_id=f"{self.source_type}:{bi}",
                            text=lines[k].strip(),
                            markdown=lines[k].rstrip(),
                            order=order,
                            filename=filename,
                            line_start=ls,
                            line_end=ls,
                            block_index=bi,
                            heading_path=list(heading_stack),
                            parent_unit_id=last_heading_id,
                        )
                    )
                i = end
                continue

            # ---- paragraph block ----
            start = i + 1
            para_lines: List[str] = []
            j = i
            while j < n and lines[j].strip():
                sl = lines[j].strip()
                if _atx_heading(sl) or _fence_info(sl) or _is_list_item(sl):
                    break
                para_lines.append(lines[j])
                j += 1
            end = j
            if end == i:
                end = i + 1
                para_lines.append(lines[i])
            bi = next_unit()
            para_md = "\n".join(para_lines).strip()
            units.append(
                _mk_unit(
                    unit_type=UnitType.PARAGRAPH,
                    unit_id=f"{self.source_type}:{bi}",
                    text=para_md,
                    markdown=para_md,
                    order=order,
                    filename=filename,
                    line_start=start,
                    line_end=end,
                    block_index=bi,
                    heading_path=list(heading_stack),
                    parent_unit_id=last_heading_id,
                )
            )
            i = end

        source.units = units
        return source


class _PlaceholderParser:
    """Minimal fill-in for parser modules that land in a later stage.

    Returns a valid ``NormalizedSource`` carrying a single explanatory unit
    plus metadata noting the dedicated parser is not yet implemented. The
    router contract stays complete and importable without those modules.
    """

    source_type = "placeholder"

    def supports(self, mime_type: str, extension: str) -> bool:
        return True

    def parse(
        self, file_path: str, filename: str, options: Optional[dict] = None
    ) -> NormalizedSource:
        source = _base_source(self.source_type, filename)
        source.metadata["placeholder"] = True
        source.metadata["pending_parser"] = self.source_type
        source.units.append(
            ContentUnit(
                unit_id=f"{self.source_type}:placeholder",
                unit_type=UnitType.PARAGRAPH,
                text=f"[{self.source_type}]: dedicated parser not yet implemented",
                order=0,
                locator=SourceLocator(file_path=filename),
            )
        )
        return source


class ImageParser(_PlaceholderParser):
    source_type = "image"


class CodeParser(_PlaceholderParser):
    source_type = "code"


def default_parser_registry() -> Dict[str, DocumentParser]:
    """Maps source_type -> a ``DocumentParser``-conforming parser instance."""
    return {
        "docx": DocxParser(),
        "pdf": PdfParser(),
        "plain_text": PlainTextParser(),
        "markdown": MarkdownParser(),
        "image": ImageParser(),
        "code": CodeParser(),
    }


def default_supported_types() -> List[str]:
    return list(default_parser_registry().keys())


class ParserRouter:
    """Selects and runs a parser, applying timeout and output-size limits.

    ``registry`` is a dict mapping detected source_type -> parser instance.
    ``timeout_seconds`` and ``max_output_chars`` default to the configured
    ``settings.PARSER_TIMEOUT_SECONDS`` / ``settings.MAX_PARSED_TEXT_CHARS``.
    """

    def __init__(
        self,
        registry: Optional[Dict[str, DocumentParser]] = None,
        timeout_seconds: Optional[float] = None,
        max_output_chars: Optional[int] = None,
    ):
        self._registry = registry or default_parser_registry()
        self.timeout_seconds = (
            settings.PARSER_TIMEOUT_SECONDS
            if timeout_seconds is None
            else timeout_seconds
        )
        self.max_output_chars = (
            settings.MAX_PARSED_TEXT_CHARS
            if max_output_chars is None
            else max_output_chars
        )
        self._executor = ThreadPoolExecutor(max_workers=4)

    # --- detection --------------------------------------------------------

    def _read_magic(self, file_path: Optional[str], n: int = 16) -> bytes:
        if not file_path:
            return b""
        try:
            with open(file_path, "rb") as fh:
                return fh.read(n)
        except OSError:
            return b""

    def detect_source_type(
        self,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> Optional[str]:
        """Resolves the source_type for a file, applying trust rules above."""
        ext_type = _type_from_extension(filename)
        mime_type_derived = _mime_type_from_mime(mime_type)
        magic_type = _classify_magic(self._read_magic(file_path))

        if magic_type == "zip":
            # ZIP container: only route to DOCX when an office document is
            # already suggested by extension or MIME; otherwise ambiguous.
            if ext_type == "docx":
                magic_type = "docx"
            elif mime_type_derived == "docx":
                magic_type = "docx"
            else:
                magic_type = None

        if magic_type:
            # Actual detected content wins over any claimed extension/MIME.
            return magic_type

        if ext_type and mime_type_derived and ext_type != mime_type_derived:
            # Signals conflict with no authoritative magic -> do not trust
            # either blindly; the caller must resolve the ambiguity.
            raise AmbiguousSourceTypeError(
                f"extension '{_extension_from_filename(filename)}' -> {ext_type!r} "
                f"conflicts with mime {mime_type!r} -> {mime_type_derived!r}; "
                "refusing to guess the actual file type"
            )

        return mime_type_derived or ext_type

    def get_parser(self, source_type: str) -> Optional[DocumentParser]:
        return self._registry.get(source_type)

    # --- execution --------------------------------------------------------

    def parse(
        self,
        file_path: str,
        filename: str,
        mime_type: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> NormalizedSource:
        """Runs the selected parser with timeout + output-size enforcement."""
        source_type = self.detect_source_type(filename, mime_type, file_path)
        if source_type is None:
            raise UnsupportedFileTypeError(
                f"unsupported file: extension={_extension_from_filename(filename)!r}"
                f" mime={mime_type!r}"
            )
        parser = self._registry.get(source_type)
        if parser is None:
            raise UnsupportedFileTypeError(
                f"no parser registered for detected type {source_type!r}"
            )

        future = self._executor.submit(parser.parse, file_path, filename, options or {})
        try:
            result = future.result(timeout=self.timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            raise ParserTimeoutError(
                f"parser for {source_type!r} exceeded timeout "
                f"{self.timeout_seconds}s"
            ) from exc

        if not isinstance(result, NormalizedSource):
            raise ParserError(
                f"parser for {source_type!r} did not return a NormalizedSource"
            )

        total = self._output_size(result)
        if total > self.max_output_chars:
            raise ParserOutputLimitError(
                f"parser for {source_type!r} produced {total} chars "
                f"(limit {self.max_output_chars})"
            )
        return result

    @staticmethod
    def _output_size(source: NormalizedSource) -> int:
        return sum(len(unit.text) for unit in source.units)
