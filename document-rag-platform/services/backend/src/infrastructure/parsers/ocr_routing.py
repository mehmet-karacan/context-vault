"""OCR routing helpers (Aşama 8.4).

Decides *whether* OCR is needed for a given source and which PDF pages to
route to OCR, reusing the text-coverage classification that
``pdf_parser.PdfParser`` already records in metadata
(``classification`` / ``text_coverage`` / ``pages_needing_ocr``).

The decision rules follow AKTIF_GOREV.md §8.4:

- a **digital** PDF page with sufficient text coverage -> NO OCR;
- a **low-coverage** (scanned/mostly blank) page / scanned document -> OCR;
- a **PNG/JPEG/TIFF** image -> OCR directly.

These are pure functions: all thresholds come from an injected
:class:`OcrRoutingConfig` (never hardcoded), so the same helper can be reused
by the parser flow and by tests with explicit config values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union


@dataclass(frozen=True)
class OcrRoutingConfig:
    """Thresholds / switches driving OCR routing decisions.

    All fields are configurable so callers (and tests) can tune behaviour
    without touching logic. Values mirror the PDF coverage thresholds used in
    ``pdf_parser`` (digital >= 0.75, scanned <= 0.25) and OCR confidence in
    ``src.config`` (``OCR_MIN_CONFIDENCE``).
    """

    # Master switch for the whole OCR feature (gate rollback, §16 FEATURE_OCR).
    ocr_enabled: bool = True
    # A document whose text coverage is at/above this needs no OCR.
    digital_coverage_threshold: float = 0.75
    # Coverage at/below this marks a document "scanned" (all pages -> OCR).
    scanned_coverage_threshold: float = 0.25
    # OCR confidence below this flags the result for human review.
    min_confidence: float = 0.5


def should_ocr(
    source_type: str,
    text_coverage: Optional[float] = None,
    config: Optional[OcrRoutingConfig] = None,
) -> bool:
    """Returns whether the given source should be sent to OCR.

    - ``source_type == "image"`` (PNG/JPEG/TIFF/...) -> always OCR.
    - otherwise ``text_coverage`` is compared against the configured digital
      threshold: high coverage (digital) -> no OCR; low coverage (scanned /
      mixed) -> OCR.
    - a missing/unknown ``text_coverage`` for a non-image source defaults to
      OCR (safe bias: never silently drop text we could not confirm exists).
    - when ``ocr_enabled`` is False, everything returns False (feature off).
    """
    cfg = config if config is not None else OcrRoutingConfig()
    if not cfg.ocr_enabled:
        return False
    if source_type == "image":
        return True
    if text_coverage is None:
        return True
    return text_coverage < cfg.digital_coverage_threshold


def route_pdf_pages_to_ocr(
    page_coverage: List[Union[bool, Dict[str, Any]]],
    config: Optional[OcrRoutingConfig] = None,
) -> List[int]:
    """Returns the page indices (0-based) that need OCR.

    ``page_coverage`` is a list with one entry per PDF page, either a bool
    (True = page has extractable text) or a dict carrying a text-presence
    key (``text_present`` or ``has_text``, as emitted by ``pdf_parser``'s
    ``coverage_metadata`` / ``extract_page_texts``). A page with no text is a
    scanned/blank page and routes to OCR.

    ``config`` only governs whether OCR is enabled at all; the per-page
    selection is purely "has text or not" per the §8.4 rule (digital pages
    with text are never re-OCRed).
    """
    cfg = config if config is not None else OcrRoutingConfig()
    if not cfg.ocr_enabled:
        return []
    pages: List[int] = []
    for i, entry in enumerate(page_coverage):
        if isinstance(entry, dict):
            has_text = bool(entry.get("text_present", entry.get("has_text")))
        else:
            has_text = bool(entry)
        if not has_text:
            pages.append(i)
    return pages
