"""Aşama 8.4: ImageParser + OCR routing of raster images.

Verifies the image parser's mapping into the normalized-content pipeline:

- source_type is ``image`` and a ``file_header`` unit carries image metadata;
- a successful OCR run produces an ``ocr_text`` unit with the full recognized
  text and a bounding-box ``SourceLocator`` (per recognized block), with OCR
  engine / confidence / orientation / preprocessing_steps recorded;
- low OCR confidence sets ``needs_review`` metadata;
- an unavailable / failing OCR provider emits an ``image`` unit with a
  ``needs_review`` warning instead of crashing the parse;
- ``to_dict()`` / ``from_dict()`` round-trips losslessly;
- the ``ParserRouter`` routes ``.png`` to the image parser.

Pillow is not a required dependency; when it is absent the width/height
metadata is simply omitted and the parser still works (a stub raster file +
an injected fake OCR provider is used, so no real image reading happens).
"""

from __future__ import annotations

from src.domain.normalized_content import NormalizedSource, UnitType
from src.infrastructure.ocr.base import OcrUnavailableError
from src.infrastructure.parsers.image_parser import (
    BBOX_FORMAT,
    ImageParser,
    OcrBlock,
    OcrResult,
    _overall_bbox,
)
from src.infrastructure.parsers.router import ParserRouter

FULL_TEXT = "Fatura toplamı 1000 TL\nNET tutar"
BLOCKS = [
    OcrBlock(text="Fatura toplamı", confidence=0.95, bbox=[10.0, 10.0, 120.0, 40.0], page=0),
    OcrBlock(text="1000 TL NET tutar", confidence=0.90, bbox=[10.0, 50.0, 160.0, 80.0], page=0),
]
_SUCCESS_RESULT = OcrResult(
    full_text=FULL_TEXT,
    blocks=BLOCKS,
    confidence=0.92,
    orientation={"angle": 90},
    engine="fake-tesseract",
    preprocessing_steps=["grayscale", "deskew"],
)


class _FakeProvider:
    """An OcrProvider-conforming fake returning a canned result / error."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def extract(self, image, languages, options=None):
        if self.error is not None:
            raise self.error
        return self.result


def _stub_image(tmp_path, name="photo.png") -> str:
    # A stub raster file: the parser does not decode pixels (Pillow optional)
    # and OCR is injected, so arbitrary bytes are sufficient.
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n fake-image-bytes")
    return str(path)


def _ocr_units(source):
    return [u for u in source.units if u.unit_type == UnitType.OCR_TEXT]


# --- happy path -------------------------------------------------------------


def test_successful_ocr_produces_ocr_text_with_bbox(tmp_path):
    parser = ImageParser(provider=_FakeProvider(result=_SUCCESS_RESULT))
    source = parser.parse(_stub_image(tmp_path), "photo.png")

    assert source.source_type == "image"
    assert source.title == "photo.png"
    assert source.language == "image"

    ocr_units = _ocr_units(source)
    assert ocr_units, "expected at least one ocr_text unit"

    # Full-text unit carries the recognized text and an overall bounding box.
    full = next(u for u in ocr_units if u.text == FULL_TEXT)
    assert full.locator is not None
    # Blocks are [l,t,w,h]: (10,10,120,40) -> right=130/bottom=50 and
    # (10,50,160,80) -> right=170/bottom=130, so the overall bbox is
    # [min_l, min_t, max_r-min_l, max_b-min_t] = [10, 10, 160, 120].
    assert full.locator.bbox == [10.0, 10.0, 160.0, 120.0]
    assert full.locator.page_start == 0

    # Per-block units carry their own bounding boxes for UI citation.
    block_unit = next(u for u in ocr_units if u.text == "Fatura toplamı")
    assert block_unit.locator.bbox == [10.0, 10.0, 120.0, 40.0]

    # OCR metadata recorded on the source.
    assert source.metadata["ocr"] is True
    assert source.metadata["ocr_engine"] == "fake-tesseract"
    assert source.metadata["ocr_confidence"] == 0.92
    assert source.metadata["orientation"] == {"angle": 90}
    assert source.metadata["needs_review"] is False


def test_file_header_unit_carries_image_metadata(tmp_path):
    parser = ImageParser(provider=_FakeProvider(result=_SUCCESS_RESULT))
    source = parser.parse(_stub_image(tmp_path), "diagram.jpeg")

    headers = [u for u in source.units if u.unit_type == UnitType.FILE_HEADER]
    assert headers, "expected a file_header unit"
    header = headers[0]
    assert header.metadata["language"] == "image"
    assert header.metadata["mime_type"] == "image/jpeg"
    assert header.locator.file_path == "diagram.jpeg"


# --- needs_review -----------------------------------------------------------


def test_low_confidence_sets_needs_review(tmp_path):
    low = OcrResult(
        full_text="garbled",
        blocks=[OcrBlock(text="garbled", confidence=0.2, bbox=[0, 0, 10, 10])],
        confidence=0.2,
        engine="fake",
    )
    source = ImageParser(provider=_FakeProvider(result=low)).parse(
        _stub_image(tmp_path), "photo.png"
    )
    assert source.metadata["needs_review"] is True
    assert "low OCR confidence" in source.metadata["review_reason"]
    # The recognized text is still emitted (searchable) even when flagged.
    assert any(u.unit_type == UnitType.OCR_TEXT and u.text == "garbled" for u in source.units)


def test_provider_unavailable_emits_image_unit_with_warning(tmp_path):
    # No provider resolvable -> image unit with needs_review, no crash.
    source = ImageParser().parse(_stub_image(tmp_path), "photo.png")
    assert source.metadata["needs_review"] is True
    assert source.metadata["ocr"] is False
    images = [u for u in source.units if u.unit_type == UnitType.IMAGE]
    assert images, "expected an image unit when OCR is unavailable"
    assert images[0].metadata["needs_review"] is True


def test_failing_provider_emits_image_unit_with_warning(tmp_path):
    parser = ImageParser(
        provider=_FakeProvider(error=RuntimeError("tesseract crashed"))
    )
    source = parser.parse(_stub_image(tmp_path), "photo.png")
    assert source.metadata["needs_review"] is True
    assert source.metadata["ocr"] is False
    assert "RuntimeError" in source.metadata["ocr_error"]
    images = [u for u in source.units if u.unit_type == UnitType.IMAGE]
    assert images and images[0].metadata["needs_review"] is True


# --- round trip -------------------------------------------------------------


def test_to_dict_from_dict_round_trip(tmp_path):
    parser = ImageParser(provider=_FakeProvider(result=_SUCCESS_RESULT))
    source = parser.parse(_stub_image(tmp_path), "photo.png")

    restored = NormalizedSource.from_dict(source.to_dict())
    assert restored.source_type == "image"
    assert restored.title == "photo.png"
    assert [u.unit_type.value for u in restored.units] == [
        u.unit_type.value for u in source.units
    ]
    full = next(u for u in restored.units if u.text == FULL_TEXT)
    assert full.locator.bbox == [10.0, 10.0, 160.0, 120.0]
    assert restored.metadata["needs_review"] is False


# --- router integration -----------------------------------------------------


def test_router_routes_png_to_image_parser(tmp_path):
    path = _stub_image(tmp_path, "photo.png")
    router = ParserRouter()
    assert router.detect_source_type(filename="photo.png") == "image"
    result = router.parse(path, "photo.png")
    assert result.source_type == "image"
    # Default registry has no provider -> graceful image-unit fallback.
    assert any(u.unit_type.value == "image" for u in result.units)


# --- real provider resolution (dead-code regression lock) ---------------------


def test_resolve_provider_builds_via_build_ocr_provider(monkeypatch):
    """The REAL lazy resolution path must obtain a provider through
    ``build_ocr_provider(settings)`` — NOT the dead ``get_ocr_provider()``
    name that always raised ImportError and silently degraded to
    needs_review. No provider is injected; resolution goes through the
    factory."""
    built = {}

    class _Stub:
        engine = "stub"

        def extract(self, image, languages=None, options=None):
            return "ok"

    def fake_build(settings_, registry=None):
        built["settings"] = settings_
        return _Stub()

    monkeypatch.setattr(
        "src.infrastructure.ocr.factory.build_ocr_provider", fake_build
    )
    parser = ImageParser()
    provider = parser._resolve_provider()
    assert provider is not None
    assert provider.engine == "stub"
    assert "settings" in built


def test_resolve_provider_returns_none_when_ocr_unavailable(monkeypatch):
    """When the factory raises (OCR disabled / no engine), the resolver must
    return None gracefully — no ImportError, no dead provider path."""
    def fake_build(settings_, registry=None):
        raise OcrUnavailableError("no OCR engine available")

    monkeypatch.setattr(
        "src.infrastructure.ocr.factory.build_ocr_provider", fake_build
    )
    assert ImageParser()._resolve_provider() is None


def test_parse_resolves_provider_via_factory_without_injection(tmp_path, monkeypatch):
    """End-to-end: with no provider injected, the parse still OCRs because the
    real resolution path builds a provider via the factory."""
    result = OcrResult(
        full_text="recognized by stub",
        blocks=[OcrBlock(text="recognized by stub", confidence=0.9, bbox=[0, 0, 50, 20])],
        confidence=0.9,
        engine="stub",
    )

    class _Stub:
        engine = "stub"

        def extract(self, image, languages=None, options=None):
            return result

    def fake_build(settings_, registry=None):
        return _Stub()

    monkeypatch.setattr(
        "src.infrastructure.ocr.factory.build_ocr_provider", fake_build
    )
    source = ImageParser().parse(_stub_image(tmp_path), "photo.png")
    assert source.metadata["ocr"] is True
    assert source.metadata["ocr_engine"] == "stub"
    assert any(u.unit_type == UnitType.OCR_TEXT for u in source.units)


# --- bbox [l,t,w,h] convention lock ------------------------------------------


def test_bbox_format_is_documented_l_t_w_h():
    assert BBOX_FORMAT == "[left, top, width, height]"


def test_overall_bbox_single_block_l_t_w_h():
    # A single [l,t,w,h] block yields the same [l,t,w,h] overall bbox.
    blocks = [OcrBlock(text="a", bbox=[0.0, 0.0, 100.0, 50.0])]
    assert _overall_bbox(blocks) == [0.0, 0.0, 100.0, 50.0]


def test_overall_bbox_two_block_union_l_t_w_h():
    blocks = [
        # left=10, top=20, w=40, h=30  -> right/bottom = 50, 50
        OcrBlock(text="a", bbox=[10.0, 20.0, 40.0, 30.0]),
        # left=60, top=50, w=30, h=40  -> right/bottom = 90, 90
        OcrBlock(text="b", bbox=[60.0, 50.0, 30.0, 40.0]),
    ]
    # min_left=10, min_top=20, max_right=90, max_bottom=90
    # -> width=80, height=70
    assert _overall_bbox(blocks) == [10.0, 20.0, 80.0, 70.0]


def test_overall_bbox_none_when_no_boxes():
    assert _overall_bbox([]) is None
    assert _overall_bbox([OcrBlock(text="no box")]) is None


def test_overall_bbox_ignores_blocks_without_bbox():
    blocks = [
        OcrBlock(text="no box"),
        OcrBlock(text="box", bbox=[0, 0, 100, 50]),
    ]
    assert _overall_bbox(blocks) == [0.0, 0.0, 100.0, 50.0]
