"""Structural PDF adapter via Docling (Aşama 3.3 primary path).

``DoclingPdfParser`` implements the ``DocumentParser`` port and is the
preferred structural PDF adapter: it uses Docling to recover headings, tables,
reading order and per-page bounding-box information into the shared
``NormalizedSource`` model (Bölüm 6).

Docling is a heavy optional dependency and is imported lazily at call time.
When it is not installed, ``parse`` raises :class:`DoclingUnavailableError`;
the ``PdfParser`` dispatcher (``pdf_parser``) catches that and degrades to the
limited PyPDF2 fallback.

The Docling document model is introspected defensively: structural conversion
is best-effort, and any Docling failure is surfaced as :class:`PdfParseError`
so the dispatcher can fall back rather than fail the whole document.
Page numbers are normalised to 0-based (Docling reports 1-based ``page_no``)
so they are consistent with the coverage metadata and the fallback path.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ...domain.normalized_content import (
    ContentUnit,
    Hierarchy,
    NormalizedSource,
    SourceLocator,
    UnitType,
)
from ...domain.ports import DocumentParser
from .pdf_parser import (
    PdfParseError,
    DoclingUnavailableError,
    _base_source,
    _PARSER_NAME,
    _PARSER_VERSION,
    classify_coverage,
    coverage_metadata,
    extract_page_texts,
)

_PARSER_PROFILE = "docling"

# Best-effort mapping of Docling item label -> normalized unit_type.
_LABEL_UNIT: Dict[str, UnitType] = {
    "heading": UnitType.HEADING,
    "title": UnitType.HEADING,
    "paragraph": UnitType.PARAGRAPH,
    "text": UnitType.PARAGRAPH,
    "table": UnitType.TABLE,
    "list": UnitType.LIST_ITEM,
    "list item": UnitType.LIST_ITEM,
    "code": UnitType.CODE,
    "formula": UnitType.FORMULA,
    "picture": UnitType.IMAGE,
    "caption": UnitType.IMAGE_CAPTION,
}


class DoclingPdfParser(DocumentParser):
    """Structural Docling adapter for ``.pdf`` files (Aşama 3.3)."""

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
        document = self._convert(file_path)
        pages = extract_page_texts(file_path)
        coverage, classification = classify_coverage(pages)

        source = _base_source(filename)
        source.metadata = {
            "parser": _PARSER_NAME,
            "parser_profile": _PARSER_PROFILE,
            "parser_version": _PARSER_VERSION,
            "parser_library": self._docling_version(),
            "origin": filename,
            "capabilities": {
                "headings": True,
                "tables": True,
                "reading_order": True,
                "bbox": True,
                "digital_scanned_classification": True,
                "ocr": False,
            },
        }
        source.metadata.update(coverage_metadata(pages, coverage, classification))
        self._populate_units(source, filename, document)
        return source

    # --- Docling loading / conversion ------------------------------------

    @staticmethod
    def _convert(file_path: str):
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise DoclingUnavailableError(
                "Docling is not installed; falling back to the limited "
                "PyPDF2 parser (install 'docling' to enable structural parsing)."
            ) from exc
        try:
            converter = DocumentConverter()
            result = converter.convert(file_path)
        except Exception as exc:
            raise PdfParseError(
                f"Docling structural conversion failed for '{file_path}': {exc}"
            ) from exc
        return getattr(result, "document", None)

    @staticmethod
    def _docling_version() -> str:
        try:
            import docling

            return "docling " + (
                getattr(docling, "__version__", "unknown") or "unknown"
            )
        except Exception:
            return "docling unknown"

    # --- normalized-unit population --------------------------------------

    def _populate_units(
        self, source: NormalizedSource, filename: str, document
    ) -> None:
        items = self._ordered_items(document)
        heading_path: List[str] = []
        order = 0
        for item in items:
            order += 1
            unit = self._item_unit(
                item, filename=filename, heading_path=heading_path, order=order
            )
            if unit is not None:
                source.units.append(unit)

    @staticmethod
    def _ordered_items(document) -> List:
        """Returns docling items in reading order (best-effort)."""
        if document is None:
            return []
        iter_items = getattr(document, "iter_items", None)
        if callable(iter_items):
            try:
                return [pair[0] for pair in iter_items()]
            except Exception:
                pass
        items = list(getattr(document, "texts", []) or [])
        items.extend(getattr(document, "tables", []) or [])
        return _sort_by_page(items)

    def _item_unit(self, item, filename, heading_path, order):
        text = self._item_text(item)
        if not text:
            return None
        label = str(getattr(item, "label", "") or "").strip().lower()
        unit_type = _LABEL_UNIT.get(label, UnitType.PARAGRAPH)
        page_no = self._item_page(item)  # 0-based
        bbox = self._item_bbox(item)

        locator = SourceLocator(
            file_path=filename,
            page_start=page_no,
            page_end=page_no,
            bbox=bbox,
        )

        markdown = text
        if unit_type is UnitType.HEADING:
            heading_path.append(text)
            depth = len(heading_path)
            markdown = ("#" * min(depth, 6) + " " + text).strip()
        elif unit_type is UnitType.TABLE:
            md = self._table_markdown(item)
            if md:
                markdown = md
        else:
            depth = len(heading_path)

        return ContentUnit(
            unit_id=f"{_PARSER_NAME}:{order}",
            unit_type=unit_type,
            text=text,
            markdown=markdown,
            order=order,
            hierarchy=Hierarchy(
                heading_path=list(heading_path),
                parent_unit_id=None,
                depth=depth,
            ),
            locator=locator,
        )

    # --- small helpers ----------------------------------------------------

    @staticmethod
    def _item_text(item) -> str:
        text = getattr(item, "text", None)
        if text is None:
            try:
                text = item.export_to_markdown()
            except Exception:
                text = None
        return (text or "").strip()

    @staticmethod
    def _table_markdown(item) -> str:
        try:
            md = item.export_to_markdown()
            return (md or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _item_page(item) -> Optional[int]:
        prov = getattr(item, "prov", None) or []
        for p in prov:
            page_no = getattr(p, "page_no", None)
            if isinstance(page_no, int) and page_no >= 1:
                return page_no - 1
        return None

    @staticmethod
    def _item_bbox(item) -> Optional[List[float]]:
        prov = getattr(item, "prov", None) or []
        for p in prov:
            bbox = getattr(p, "bbox", None)
            if bbox is None:
                continue
            to_list = getattr(bbox, "to_list", None)
            if callable(to_list):
                try:
                    value = to_list()
                    if isinstance(value, (list, tuple)) and len(value) == 4:
                        return [float(v) for v in value]
                except Exception:
                    pass
            try:
                value = list(bbox)
                if len(value) == 4:
                    return [float(v) for v in value]
            except Exception:
                pass
        return None


def _sort_by_page(items: List) -> List:
    def key(item):
        for p in getattr(item, "prov", None) or []:
            page_no = getattr(p, "page_no", None)
            if isinstance(page_no, int):
                return page_no
        return 10**9

    return sorted(items, key=key)
