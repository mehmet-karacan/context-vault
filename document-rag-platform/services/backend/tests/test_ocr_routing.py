"""Aşama 8.4: OCR routing decisions.

Verifies the pure OCR-routing helpers:

- ``should_ocr``: image source_type -> always OCR; digital (high text
  coverage) -> no OCR; scanned (low coverage) -> OCR; OCR-feature-off ->
  never OCR.
- ``route_pdf_pages_to_ocr``: selects exactly the low-coverage (scanned)
  pages by index, in order.
- thresholds come from an injected ``OcrRoutingConfig`` — changing a
  threshold changes the decision (nothing hardcoded).

Also covers the optional ``ocr_json`` artifact persistence through an
in-memory ``ObjectStorage`` (DB-free).
"""

from __future__ import annotations

import json
from typing import Optional

from src.infrastructure.parsers.image_parser import OcrBlock, OcrResult
from src.infrastructure.parsers.ocr_artifact import (
    OCR_ARTIFACT_RELATIVE_PATH,
    ocr_artifact_key,
    persist_ocr_json,
)
from src.infrastructure.parsers.ocr_routing import (
    OcrRoutingConfig,
    route_pdf_pages_to_ocr,
    should_ocr,
)


# --- should_ocr -------------------------------------------------------------


def test_should_ocr_always_true_for_image_source_type():
    assert should_ocr("image", text_coverage=1.0) is True
    assert should_ocr("image", text_coverage=0.0) is True
    assert should_ocr("image") is True


def test_should_ocr_digital_high_coverage_is_false():
    # Digital PDF with sufficient coverage -> no OCR.
    assert should_ocr("pdf", text_coverage=0.95) is False


def test_should_ocr_scanned_low_coverage_is_true():
    # Scanned / low-coverage page -> OCR.
    assert should_ocr("pdf", text_coverage=0.05) is True


def test_should_ocr_unknown_coverage_bias_to_ocr():
    assert should_ocr("pdf") is True


def test_should_ocr_respects_config_threshold():
    # A config with a stricter digital threshold forces OCR at 0.9 coverage.
    strict = OcrRoutingConfig(digital_coverage_threshold=0.95)
    assert should_ocr("pdf", text_coverage=0.9, config=strict) is True
    # Default config at 0.9 coverage sees it as digital -> no OCR.
    assert should_ocr("pdf", text_coverage=0.9) is False


def test_should_ocr_feature_disabled_always_false():
    off = OcrRoutingConfig(ocr_enabled=False)
    assert should_ocr("image", config=off) is False
    assert should_ocr("pdf", text_coverage=0.0, config=off) is False


# --- route_pdf_pages_to_ocr -------------------------------------------------


def test_routes_exactly_low_coverage_pages():
    page_coverage = [
        {"text_present": True},   # page 0 - digital
        {"text_present": False},  # page 1 - scanned -> OCR
        {"text_present": True},   # page 2 - digital
        {"has_text": False},      # page 3 - scanned -> OCR (alt key)
    ]
    assert route_pdf_pages_to_ocr(page_coverage) == [1, 3]


def test_routes_all_pages_for_fully_scanned_document():
    coverage = [False, False, False]
    assert route_pdf_pages_to_ocr(coverage) == [0, 1, 2]


def test_routes_none_for_fully_digital_document():
    coverage = [True, True, True]
    assert route_pdf_pages_to_ocr(coverage) == []


def test_feature_disabled_routes_nothing():
    off = OcrRoutingConfig(ocr_enabled=False)
    assert route_pdf_pages_to_ocr([False, False], config=off) == []


# --- ocr_json artifact ------------------------------------------------------


class _InMemoryStorage:
    """Minimal in-memory ``ObjectStorage`` fake (DB-free)."""

    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type=None):
        self.objects[key] = data
        return key

    def get(self, key):
        return self.objects[key]

    def delete(self, key):
        self.objects.pop(key, None)

    def exists(self, key):
        return key in self.objects


def test_persist_ocr_json_via_in_memory_storage():
    storage = _InMemoryStorage()
    result = OcrResult(
        full_text="SATIR 1",
        blocks=[OcrBlock(text="SATIR 1", confidence=0.9, bbox=[1.0, 2.0, 3.0, 4.0])],
        confidence=0.9,
        engine="fake",
    )
    key = persist_ocr_json(
        storage,
        project_id="p-1",
        document_id="d-1",
        version_id="v-1",
        ocr_result=result,
        extra_metadata={"source_type": "image", "needs_review": False},
    )
    expected = ocr_artifact_key("p-1", "d-1", "v-1")
    assert key == expected
    assert OCR_ARTIFACT_RELATIVE_PATH in expected
    assert expected.startswith("projects/p-1/documents/d-1/versions/v-1/artifacts/")
    assert storage.exists(key) is True

    payload = json.loads(storage.get(key).decode("utf-8"))
    assert payload["full_text"] == "SATIR 1"
    assert payload["blocks"][0]["bbox"] == [1.0, 2.0, 3.0, 4.0]
    assert payload["metadata"] == {"source_type": "image", "needs_review": False}
