"""Code parser (Aşama 7.5) — line/symbol-aware source parsing.

Parses a source file (detected by extension/language) into a shared
``NormalizedSource`` carrying:

* a ``file_header`` unit whose ``metadata`` holds the file path, language and
  MIME type;
* ``code`` unit(s) preserving the exact source line range
  (``SourceLocator.line_start`` / ``line_end``, ``file_path``, ``block_index``);
* ``symbol`` units for the detectable top-level declarations (function / class /
  method / package / procedure signatures), each carrying ``symbol_name``,
  ``symbol_type`` and its line range.

Symbol detection is deliberately pattern/line-scan based with **no** tree-sitter
dependency (tree-sitter is optional and deferred — see the module note in
``infrastructure/chunkers/code_chunker.py``). For PL/SQL the same
string/comment-aware strip used by the PL/SQL chunker is reused so a keyword
inside a string or comment is never mistaken for a declaration boundary.

``source_type="code"``, ``title`` is the file name and the language lives in
``metadata`` / ``language``. ``NormalizedSource`` round-trips via
``to_dict()`` / ``from_dict()``.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ...domain.normalized_content import (
    ContentUnit,
    Hierarchy,
    NormalizedSource,
    SourceLocator,
    UnitType,
)
from ...domain.ports import DocumentParser
from ..repositories.language_detection import EXTENSION_TO_LANGUAGE, detect_language
from ..chunkers.plsql_chunker import strip_plsql_lines

__all__ = ["CodeParser"]

# Extension -> language slug (subset relevant to the code parser).
_EXT_LANG: Dict[str, str] = {
    ext: lang for ext, lang in EXTENSION_TO_LANGUAGE.items() if ext != "txt"
}

# MIME types that identify code sources.
_CODE_MIME: frozenset = frozenset(
    {
        "application/json",
        "text/json",
        "application/x-yaml",
        "text/yaml",
        "text/x-yaml",
        "application/javascript",
        "application/x-javascript",
        "text/javascript",
        "text/x-python",
        "text/python",
        "application/x-python",
        "text/x-java-source",
        "text/x-java",
        "text/x-sql",
        "application/sql",
        "text/x-plsql",
        "application/x-plsql",
        "text/x-csrc",
        "text/x-c++src",
        "text/x-c",
        "application/xml",
        "text/xml",
        "text/html",
        "text/x-sh",
        "text/x-ruby",
    }
)

# --- per-language top-level symbol detection ------------------------------
#
# Each detector returns a list of ``(line_index, symbol_name, symbol_type)`` for
# top-level declarations. ``line_index`` is 0-based.

_PY_RE = re.compile(r"^(?:async\s+)?(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_JS_FUNC_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_CLASS_RE = re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)")
_JAVA_TYPE_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed|strictfp)\s+)*"
    r"(?:class|interface|enum|record|@interface)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_JAVA_METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected)\s+"
    r"(?:static\s+|final\s+|abstract\s+|synchronized\s+)*"
    r"[A-Za-z_][\w<>\[\],.\s]*?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_PLSQL_CREATE_RE = re.compile(
    r"^CREATE\s+(OR\s+REPLACE\s+)?"
    r"(?P<kind>PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE\s+BODY|TYPE)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_$#]*)",
    re.IGNORECASE,
)


def _detect_python(lines: List[str]) -> List[Tuple[int, str, str]]:
    out: List[Tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        m = _PY_RE.match(line)
        if m:
            out.append((i, m.group(2), "class" if m.group(1) == "class" else "function"))
    return out


def _detect_js(lines: List[str]) -> List[Tuple[int, str, str]]:
    out: List[Tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        m = _JS_CLASS_RE.match(line)
        if m:
            out.append((i, m.group(1), "class"))
            continue
        m = _JS_FUNC_RE.match(line)
        if m:
            out.append((i, m.group(1), "function"))
    return out


def _detect_java(lines: List[str]) -> List[Tuple[int, str, str]]:
    out: List[Tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        m = _JAVA_TYPE_RE.match(line)
        if m:
            out.append((i, m.group(1), "class"))
            continue
        m = _JAVA_METHOD_RE.match(line)
        if m and line.lstrip().startswith(("public", "private", "protected")):
            out.append((i, m.group(1), "method"))
    return out


def _detect_plsql(lines: List[str], cleaned: List[str]) -> List[Tuple[int, str, str]]:
    out: List[Tuple[int, str, str]] = []
    for i, cl in enumerate(cleaned):
        m = _PLSQL_CREATE_RE.match(cl)
        if m:
            kind = re.sub(r"\s+", " ", m.group("kind").strip().upper())
            name = re.split(r"\s+", m.group("name"))[0].upper()
            out.append((i, name, kind))
    return out


def _detect_config(lines: List[str]) -> List[Tuple[int, str, str]]:
    """Top-level keys of YAML/JSON/TOML/XML as symbol-like units."""
    out: List[Tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if not line or line[0] in " #\t}\】":
            continue
        stripped = line.lstrip()
        if stripped.startswith("%YAML") or stripped.startswith("---"):
            continue
        m = re.match(r'^"([^"]+)"\s*:', stripped)  # JSON key
        if not m:
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_.-]*):", stripped)  # YAML/TOML key
        if m:
            out.append((i, m.group(1), "config_key"))
            continue
        m = re.match(r"^<([A-Za-z_][A-Za-z0-9_.-]*)", stripped)  # XML tag
        if m:
            out.append((i, m.group(1), "config_element"))
    return out


_LANG_DETECTORS: Dict[str, Any] = {
    "python": _detect_python,
    "javascript": _detect_js,
    "typescript": _detect_js,
    "java": _detect_java,
    "plsql": _detect_plsql,
    "sql": _detect_plsql,
    "json": _detect_config,
    "yaml": _detect_config,
    "toml": _detect_config,
    "xml": _detect_config,
}


def _decode(raw: bytes) -> Tuple[str, str, Dict[str, Any]]:
    import codecs

    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig", {}
    for enc in ("utf-8", "cp1254", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc, {}
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8", {"fallback": "replace"}


class CodeParser:
    """Parses a source file into a ``NormalizedSource`` for source_type "code"."""

    source_type = "code"

    def supports(self, mime_type: str, extension: str) -> bool:
        ext = (extension or "").lower().lstrip(".")
        mime = (mime_type or "").split(";", 1)[0].strip().lower()
        if ext in _EXT_LANG:
            return True
        if mime in _CODE_MIME:
            return True
        return False

    def parse(
        self, file_path: str, filename: str, options: Optional[dict] = None
    ) -> NormalizedSource:
        options = options or {}
        with open(file_path, "rb") as fh:
            raw = fh.read()
        text, encoding, enc_info = _decode(raw)

        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        language = str(options["language"]) if options.get("language") else _EXT_LANG.get(ext)
        if language is None:
            language = "plaintext"
        # Normalise PL/SQL-ish sql family for the parser layer.
        language_slug = "plsql" if language == "sql" else language

        source = NormalizedSource(
            source_id=str(uuid.uuid4()),
            source_type="code",
            title=filename,
            language=language_slug,
            metadata={
                "file_path": file_path,
                "language": language_slug,
                "extension": ext,
                "mime": options.get("mime_type") or _mime_hint(ext),
                "encoding": encoding,
                "line_count": len(text.splitlines()),
                **enc_info,
            },
        )

        units: List[ContentUnit] = []
        order = 0
        block_index = 0

        # file_header unit
        order += 1
        block_index += 1
        units.append(
            ContentUnit(
                unit_id=f"{self.source_type}:header",
                unit_type=UnitType.FILE_HEADER,
                text=filename,
                markdown=f"`{filename}`",
                order=order,
                hierarchy=Hierarchy(heading_path=[filename], depth=1),
                locator=SourceLocator(
                    file_path=file_path, line_start=1, line_end=1, block_index=block_index
                ),
                metadata={
                    "file_path": file_path,
                    "language": language_slug,
                    "mime": source.metadata["mime"],
                },
            )
        )

        # single code unit preserving the whole-file line range
        order += 1
        block_index += 1
        lines = text.splitlines()
        n = len(lines)
        code_unit = ContentUnit(
            unit_id=f"{self.source_type}:body",
            unit_type=UnitType.CODE,
            text="\n".join(lines),
            markdown=f"```{ext}\n{text.rstrip()}\n```",
            order=order,
            hierarchy=Hierarchy(heading_path=[filename], depth=1),
            locator=SourceLocator(
                file_path=file_path,
                line_start=1,
                line_end=n,
                block_index=block_index,
            ),
            metadata={"language": language_slug, "extension": ext},
        )
        units.append(code_unit)

        # symbol units
        detector = _LANG_DETECTORS.get(language_slug)
        if detector:
            if language_slug in ("plsql", "sql"):
                cleaned = strip_plsql_lines(text)
                symbols = detector(lines, cleaned)
            else:
                symbols = detector(lines)
            for pos, (idx, sym_name, sym_type) in enumerate(symbols):
                end_idx = symbols[pos + 1][0] - 1 if pos + 1 < len(symbols) else n - 1
                end_idx = max(end_idx, idx)
                order += 1
                block_index += 1
                units.append(
                    ContentUnit(
                        unit_id=f"{self.source_type}:symbol:{idx}",
                        unit_type=UnitType.SYMBOL,
                        text="\n".join(lines[idx : end_idx + 1]),
                        markdown=None,
                        order=order,
                        hierarchy=Hierarchy(heading_path=[filename, sym_name], depth=2),
                        locator=SourceLocator(
                            file_path=file_path,
                            line_start=idx + 1,
                            line_end=end_idx + 1,
                            symbol_name=sym_name,
                            symbol_type=sym_type,
                            block_index=block_index,
                        ),
                        metadata={
                            "language": language_slug,
                            "symbol_name": sym_name,
                            "symbol_type": sym_type,
                            "signature": lines[idx].strip(),
                        },
                    )
                )

        source.units = units
        return source


def _mime_hint(ext: str) -> str:
    import mimetypes

    return mimetypes.guess_type(f"f.{ext}")[0] or "text/plain"
