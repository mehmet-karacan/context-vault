"""Aşama 3.3: structural PDF parser tests.

Builds PDF fixtures programmatically with a minimal, dependency-free PDF
writer (no reportlab) so each page carries controllable text (or is blank),
then asserts:

- digital / scanned / mixed classification via text-coverage;
- per-page locator accuracy (``page_start``/``page_end``);
- content units carry their originating page number;
- sufficient text pages vs blank pages round-trip into coverage metadata;
- the limited PyPDF2 fallback profile and capability marker are recorded;
- Docling is imported lazily and degrades gracefully when unavailable;
- ``NormalizedSource`` ``to_dict()``/``from_dict()`` round-trip losslessly.
"""

import importlib
import json

import pytest

from pdf_fixtures import build_pdf
from src.domain.normalized_content import NormalizedSource, UnitType
from src.infrastructure.parsers.docling_parser import (
    DoclingPdfParser,
    DoclingUnavailableError,
)
from src.infrastructure.parsers.pdf_parser import PdfParser, classify_coverage

TEXT_1 = "This is the first page and it carries plenty of meaningful digital text."
TEXT_2 = "The second page continues with a good deal of readable digital content."


@pytest.fixture
def digital_pdf(tmp_path):
    pdf = tmp_path / "digital.pdf"
    pdf.write_bytes(build_pdf([TEXT_1, TEXT_2, "Third page text here as well."]))
    return str(pdf)


@pytest.fixture
def scanned_pdf(tmp_path):
    pdf = tmp_path / "scanned.pdf"
    pdf.write_bytes(build_pdf(["", "", ""]))
    return str(pdf)


@pytest.fixture
def mixed_pdf(tmp_path):
    pdf = tmp_path / "mixed.pdf"
    pdf.write_bytes(build_pdf([TEXT_1, "", TEXT_2, ""]))
    return str(pdf)


# --- parser contract -------------------------------------------------------


def test_supports_pdf_extension_and_mime():
    parser = PdfParser()
    assert parser.supports("application/pdf", ".pdf")
    assert parser.supports("", ".PDF")
    assert parser.supports("APPLICATION/PDF; charset=binary", ".pdf")
    assert not parser.supports("text/plain", ".txt")
    assert parser.supports("application/pdf", "")
    assert not parser.supports("", ".txt")


def test_source_shape_is_document(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(build_pdf([TEXT_1]))
    source = PdfParser().parse(str(pdf), "x.pdf")
    assert source.source_type == "document"
    assert source.title == "x.pdf"
    assert source.language is None
    assert source.metadata["parser"] == "pdf"
    assert source.metadata["parser_profile"] == "pypdf2-fallback"
    assert source.metadata["origin"] == "x.pdf"


# --- digital / scanned / mixed classification ------------------------------


def test_digital_pdf_classified_and_units_carry_page_numbers(digital_pdf):
    source = PdfParser().parse(digital_pdf, "digital.pdf")
    assert source.metadata["classification"] == "digital"
    assert source.metadata["text_coverage"] == 1.0
    assert source.metadata["page_count"] == 3
    assert source.metadata["pages_needing_ocr"] == []
    assert source.metadata["ocr_recommended"] is False

    pages = sorted(u.locator.page_start for u in source.units)
    assert pages == [0, 1, 2]
    assert all(u.locator.page_start == u.locator.page_end for u in source.units)
    assert all(u.locator.bbox is None for u in source.units)


def test_scanned_pdf_classified_and_no_text_units(scanned_pdf):
    source = PdfParser().parse(scanned_pdf, "scanned.pdf")
    assert source.metadata["classification"] == "scanned"
    assert source.metadata["text_coverage"] == 0.0
    assert source.metadata["pages_needing_ocr"] == [0, 1, 2]
    assert source.metadata["ocr_recommended"] is True
    assert source.units == []


def test_mixed_pdf_routes_only_low_coverage_pages(mixed_pdf):
    source = PdfParser().parse(mixed_pdf, "mixed.pdf")
    assert source.metadata["classification"] == "mixed"
    assert source.metadata["text_coverage"] == 0.5
    assert source.metadata["pages_needing_ocr"] == [1, 3]
    assert source.metadata["ocr_recommended"] is True
    assert sorted(u.locator.page_start for u in source.units) == [0, 2]


def test_classify_coverage_helper_directly():
    text_page = {"has_text": True}
    blank_page = {"has_text": False}
    coverage, cls = classify_coverage([text_page] * 3)
    assert (coverage, cls) == (1.0, "digital")
    coverage, cls = classify_coverage([blank_page] * 3)
    assert (coverage, cls) == (0.0, "scanned")
    coverage, cls = classify_coverage([text_page, blank_page, text_page, blank_page])
    assert (coverage, cls) == (0.5, "mixed")


# --- fallback unit content ------------------------------------------------


def test_fallback_preserves_reading_order_by_page(digital_pdf):
    source = PdfParser().parse(digital_pdf, "digital.pdf")
    orders = [u.order for u in source.units]
    assert orders == sorted(orders)
    assert all(u.unit_type == UnitType.PARAGRAPH for u in source.units)


def test_blank_pages_produce_no_units_but_still_metadata(mixed_pdf):
    source = PdfParser().parse(mixed_pdf, "mixed.pdf")
    assert len(source.units) == 2  # only the two text pages
    assert all(
        u.locator.page_start not in source.metadata["pages_needing_ocr"]
        for u in source.units
    )


def test_page_coverage_metadata_is_recorded_per_page(digital_pdf):
    source = PdfParser().parse(digital_pdf, "digital.pdf")
    page_coverage = source.metadata["page_coverage"]
    assert [p["page"] for p in page_coverage] == [0, 1, 2]
    assert all(p["text_present"] is True for p in page_coverage)
    assert all(p["char_count"] >= 20 for p in page_coverage)


# --- capability marker -----------------------------------------------------


def test_fallback_profile_records_capabilities(digital_pdf):
    source = PdfParser().parse(digital_pdf, "digital.pdf")
    caps = source.metadata["capabilities"]
    assert caps["reading_order_by_page"] is True
    assert caps["digital_scanned_classification"] is True
    assert caps["ocr"] is False
    assert caps["bbox"] is False
    assert any("fallback" in w for w in caps["warnings"])


# --- force fallback option (even if docling were installed) ---------------


def test_force_fallback_option_disables_docling_path(digital_pdf):
    source = PdfParser().parse(digital_pdf, "digital.pdf", options={"force_fallback": True})
    assert source.metadata["parser_profile"] == "pypdf2-fallback"


# --- Docling lazy import / graceful degradation ---------------------------


def test_docling_parser_module_imports_without_docling_installed():
    mod = importlib.import_module("src.infrastructure.parsers.docling_parser")
    assert hasattr(mod, "DoclingPdfParser")


def test_docling_adapter_is_lazy_and_raises_unavailable(digital_pdf):
    adapter = DoclingPdfParser()
    with pytest.raises(DoclingUnavailableError):
        adapter.parse(digital_pdf, "digital.pdf")


def test_pdf_parser_falls_back_gracefully_when_docling_unavailable(digital_pdf):
    source = PdfParser().parse(digital_pdf, "digital.pdf")
    assert source.metadata["parser_profile"] == "pypdf2-fallback"


# --- JSON round trip -------------------------------------------------------


def test_normalized_source_round_trips_losslessly(digital_pdf):
    source = PdfParser().parse(digital_pdf, "digital.pdf")
    payload = json.dumps(source.to_dict(), ensure_ascii=False)
    restored = NormalizedSource.from_dict(json.loads(payload))
    assert restored == source
    assert restored.units == source.units
