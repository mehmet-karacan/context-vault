"""Structural PDF parser (Aşama 3.3).

Implements the ``DocumentParser`` port (``domain.ports``) for PDF documents
and returns a shared ``NormalizedSource`` (Bölüm 6).

Behavior follows AKTIF_GOREV.md 3.3:

- **Digital vs scanned classification.** Every page's text is extracted with
  PyPDF2 and a text-coverage ratio is computed (``text pages / total pages``).
  Coverage drives the ``digital`` / ``scanned`` / ``mixed`` classification
  that is recorded in source metadata together with a per-page coverage table.
  This stage only classifies and records coverage; the actual OCR *routing*
  decision (which low-coverage pages to send to OCR) belongs to Aşama 8.
- **Docling-first.** When the (heavy) Docling adapter is importable and the
  caller has not forced the fallback, parsing is delegated to
  ``docling_parser.DoclingPdfParser`` which yields headings, tables, reading
  order and page + bounding-box information. Docling is imported lazily inside
  the adapter and the dispatcher here degrades gracefully to the fallback when
  Docling is unavailable or fails.
- **Limited PyPDF2 fallback.** Emits paragraph units carrying an accurate
  ``page_start``/``page_end`` (0-based page index), best-effort empty
  ``heading_path``, and preserves reading order page by page. The capability
  marker ``parser_profile="pypdf2-fallback"`` is recorded in metadata.
- Page numbers are accurate in ``SourceLocator.page_start``/``page_end`` and
  carried into every content unit that originates on a page (3.3: "PDF
  citation sayfa numarası doğru").

Both adapters return ``NormalizedSource.source_type="document"`` with the
parsing-specific routing type (``pdf``) only meaningful at the router/registry
level.
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional, Tuple

from ...domain.normalized_content import (
    ContentUnit,
    Hierarchy,
    NormalizedSource,
    SourceLocator,
    UnitType,
)
from ...domain.ports import DocumentParser

_PARSER_NAME = "pdf"
_FALLBACK_PROFILE = "pypdf2-fallback"
_PARSER_VERSION = "0.1.0"

# A page is treated as "having text" when its extracted text has at least this
# many meaningful characters; anything below counts as a blank / scanned page.
_MIN_TEXT_LEN = 20

# Text-coverage classification thresholds (AKTIF_GOREV.md 3.3).
_DIGITAL_THRESHOLD = 0.75
_SCANNED_THRESHOLD = 0.25


class PdfParseError(Exception):
    """Base class for PDF parsing failures."""


class UnreadablePdfError(PdfParseError):
    """Raised when a PDF cannot be opened / read at all (corrupt or not PDF)."""


class DoclingUnavailableError(PdfParseError):
    """Raised when Docling is required but not installed."""


# --- shared text-coverage helpers -----------------------------------------


def extract_page_texts(file_path: str, min_text_len: int = _MIN_TEXT_LEN) -> List[Dict]:
    """Extracts text per page with PyPDF2, returning structured page info.

    Each entry: ``{"page": i, "paragraphs": [...], "text", "char_count",
    "has_text"}``. ``page`` is a 0-based page index.
    """
    from PyPDF2 import PdfReader  # PyPDF2 is the light always-available dep.

    try:
        reader = PdfReader(file_path)
    except Exception as exc:  # MissingPdfReadError / PyPdfError / OSError ...
        raise UnreadablePdfError(
            f"could not read PDF '{file_path}': {exc}"
        ) from exc

    pages: List[Dict] = []
    for i, page in enumerate(reader.pages):
        raw = ""
        try:
            raw = page.extract_text() or ""
        except Exception:  # per-page extraction is best-effort
            raw = ""
        paragraphs = _split_paragraphs(raw)
        text = " ".join(paragraphs)
        char_count = len(text)
        pages.append(
            {
                "page": i,
                "paragraphs": paragraphs,
                "text": text,
                "char_count": char_count,
                "has_text": char_count >= min_text_len,
            }
        )
    return pages


def _split_paragraphs(raw: str) -> List[str]:
    """Groups consecutive non-blank lines into paragraphs preserving order."""
    paragraphs: List[str] = []
    current: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def classify_coverage(
    pages: List[Dict],
    digital_threshold: float = _DIGITAL_THRESHOLD,
    scanned_threshold: float = _SCANNED_THRESHOLD,
) -> Tuple[float, str]:
    """Returns ``(coverage, classification)`` from per-page data.

    ``coverage`` is the ratio of text-bearing pages to total pages.
    ``classification`` is ``"digital"`` (high coverage), ``"scanned"`` (low
    coverage) or ``"mixed"`` (in between).
    """
    total = len(pages)
    text_pages = sum(1 for p in pages if p.get("has_text"))
    coverage = (text_pages / total) if total else 0.0
    if coverage >= digital_threshold:
        classification = "digital"
    elif coverage <= scanned_threshold:
        classification = "scanned"
    else:
        classification = "mixed"
    return coverage, classification


def coverage_metadata(pages: List[Dict], coverage: float, classification: str) -> Dict:
    """Builds the coverage / OCR-routing metadata block shared by both paths."""
    page_coverage = [
        {
            "page": p["page"],
            "text_present": p["has_text"],
            "char_count": p["char_count"],
        }
        for p in pages
    ]
    ocr_pages = [p["page"] for p in pages if not p["has_text"]]
    return {
        "classification": classification,
        "text_coverage": coverage,
        "page_count": len(pages),
        "page_coverage": page_coverage,
        # Aşama 8 routing input: high-coverage docs need no OCR, mixed docs
        # route only these low-coverage pages, scanned docs route all pages.
        "ocr_recommended": classification != "digital",
        "pages_needing_ocr": ocr_pages,
    }


def _base_source(filename: Optional[str]) -> NormalizedSource:
    return NormalizedSource(
        source_id=str(uuid.uuid4()),
        source_type="document",
        title=filename,
        language=None,
    )


class PdfParser(DocumentParser):
    """Dispatcher parser for ``.pdf`` files (Aşama 3.3).

    Prefers the Docling structural adapter when it is available, otherwise
    falls back to a limited PyPDF2 parser. Always records which parser profile
    actually handled the file plus the digital/scanned text-coverage metadata.
    """

    source_type = "pdf"

    def supports(self, mime_type: str, extension: str) -> bool:
        ext = (extension or "").lower()
        if ext in (".pdf",):
            return True
        mime = (mime_type or "").split(";", 1)[0].strip().lower()
        return mime == "application/pdf"

    def parse(
        self,
        file_path: str,
        filename: str,
        options: Optional[dict] = None,
    ) -> NormalizedSource:
        options = options or {}
        if self._docling_enabled(options):
            try:
                from .docling_parser import DoclingPdfParser

                return DoclingPdfParser().parse(file_path, filename, options)
            except (ImportError, DoclingUnavailableError, PdfParseError):
                # Docling missing or structural conversion failed -> degrade
                # to the limited fallback instead of failing the document.
                pass
        return self._parse_fallback(file_path, filename, options)

    # --- dispatch policy --------------------------------------------------

    @staticmethod
    def _docling_enabled(options: dict) -> bool:
        if options.get("force_fallback"):
            return False
        try:
            import docling  # noqa: F401  (lazy, heavy import probe)

            return True
        except ImportError:
            return False

    # --- PyPDF2 fallback --------------------------------------------------

    def _parse_fallback(
        self,
        file_path: str,
        filename: str,
        options: Optional[dict] = None,
    ) -> NormalizedSource:
        pages = extract_page_texts(file_path)
        coverage, classification = classify_coverage(pages)
        source = _base_source(filename)
        source.metadata = {
            "parser": _PARSER_NAME,
            "parser_profile": _FALLBACK_PROFILE,
            "parser_version": _PARSER_VERSION,
            "parser_library": _pypdf2_version(),
            "origin": filename,
            "capabilities": {
                "headings": False,
                "tables": False,
                "reading_order_by_page": True,
                "bbox": False,
                "digital_scanned_classification": True,
                "ocr": False,
                "warnings": [
                    "Docling unavailable or disabled; limited fallback used"
                ],
            },
        }
        source.metadata.update(coverage_metadata(pages, coverage, classification))
        self._emit_units(source, filename, pages)
        return source

    @staticmethod
    def _emit_units(
        source: NormalizedSource,
        filename: str,
        pages: List[Dict],
    ) -> None:
        order = 0
        for page in pages:
            for paragraph in page["paragraphs"]:
                order += 1
                source.units.append(
                    ContentUnit(
                        unit_id=f"{_PARSER_NAME}:{order}",
                        unit_type=UnitType.PARAGRAPH,
                        text=paragraph,
                        markdown=paragraph,
                        order=order,
                        hierarchy=Hierarchy(heading_path=[], depth=0),
                        locator=SourceLocator(
                            file_path=filename,
                            page_start=page["page"],
                            page_end=page["page"],
                        ),
                    )
                )


def _pypdf2_version() -> str:
    try:
        import PyPDF2

        return "pypdf2 " + (getattr(PyPDF2, "__version__", "unknown") or "unknown")
    except Exception:
        return "pypdf2 unknown"
