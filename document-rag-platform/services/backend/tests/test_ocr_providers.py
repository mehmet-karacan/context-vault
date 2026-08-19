"""OCR provider + factory tests (Aşama 8.2).

Covers lazy-availability handling: Docling/tesseract/pytesseract are optional
heavy deps that may be absent, in which case the provider reports unavailable
and ``extract`` raises a clear error; the factory degrades to the configured
fallback. The actual tesseract OCR round is attempted only when the binary is
really present (otherwise skipped). All tests are deterministic and require no
internet access.
"""

import pytest
from types import SimpleNamespace

from src.infrastructure.ocr import (
    OcrConfigurationError,
    OcrUnavailableError,
    DoclingOcrProvider,
    DoclingUnavailableError,
    TesseractOcrProvider,
    TesseractUnavailableError,
    build_ocr_provider,
)
from src.infrastructure.ocr.factory import DEFAULT_REGISTRY


def make_settings(provider="docling", fallback="tesseract", feature=True, enabled=True):
    return SimpleNamespace(
        FEATURE_OCR=feature,
        OCR_ENABLED=enabled,
        OCR_PROVIDER=provider,
        OCR_FALLBACK_PROVIDER=fallback,
    )


# --- Docling ----------------------------------------------------------------


def test_docling_provider_reports_unavailable_when_missing():
    provider = DoclingOcrProvider()
    assert provider.engine == "docling"
    if not provider.available:
        assert provider.engine_version is None
    else:
        assert "docling" in provider.engine_version


def test_docling_extract_raises_unavailable_when_missing():
    provider = DoclingOcrProvider()
    if provider.available:
        pytest.skip("docling installed; availability error path not exercisable here")
    with pytest.raises(DoclingUnavailableError):
        provider.extract(None, languages=["tur", "eng"])


# --- Tesseract --------------------------------------------------------------


def test_tesseract_provider_reports_engine_attr_and_version():
    provider = TesseractOcrProvider()
    assert provider.engine == "tesseract"
    assert provider.languages == ["tur", "eng"]
    if not provider.available:
        assert provider.engine_version is None
    else:
        assert "tesseract" in provider.engine_version


def test_tesseract_extract_raises_unavailable_when_missing():
    provider = TesseractOcrProvider()
    if provider.available:
        pytest.skip("tesseract installed; unavailable path not exercisable here")
    with pytest.raises(TesseractUnavailableError):
        provider.extract(None, languages=["tur", "eng"])


def test_actual_tesseract_ocr_round_when_binary_present():
    provider = TesseractOcrProvider()
    if not provider.available:
        pytest.skip("tesseract binary not available; skipping real OCR round")
    from PIL import Image, ImageDraw

    img = Image.new("L", (200, 80), 255)
    draw = ImageDraw.Draw(img)
    draw.text((10, 30), "Hello", fill=0)
    result = provider.extract(img, languages=["eng"])
    assert result.engine == "tesseract"
    assert "Hello" in result.full_text
    assert result.blocks


# --- Factory -----------------------------------------------------------------


class _FakeUnavailable:
    available = False
    engine = "fake-unavailable"

    def __init__(self, *args, **kwargs):
        pass


class _FakeAvailable:
    available = True
    engine = "fake-available"

    def __init__(self, *args, **kwargs):
        pass


def test_factory_returns_fallback_when_primary_unavailable():
    registry = {
        "docling": _FakeUnavailable,
        "tesseract": _FakeAvailable,
    }
    provider = build_ocr_provider(
        make_settings(provider="docling", fallback="tesseract"),
        registry=registry,
    )
    assert provider is not None
    assert provider.engine == "fake-available"


def test_factory_returns_primary_when_available():
    registry = {"docling": _FakeAvailable, "tesseract": _FakeUnavailable}
    provider = build_ocr_provider(
        make_settings(provider="docling", fallback="tesseract"),
        registry=registry,
    )
    assert provider.engine == "fake-available"


def test_factory_unknown_provider_clear_error():
    with pytest.raises(OcrConfigurationError):
        build_ocr_provider(
            make_settings(provider="no-such-provider"),
            registry={"tesseract": _FakeAvailable},
        )


def test_factory_unavailable_everywhere_clear_error():
    registry = {"docling": _FakeUnavailable, "tesseract": _FakeUnavailable}
    with pytest.raises(OcrUnavailableError):
        build_ocr_provider(
            make_settings(provider="docling", fallback="tesseract"),
            registry=registry,
        )


def test_factory_feature_gate_disabled_raises():
    with pytest.raises(OcrUnavailableError):
        build_ocr_provider(
            make_settings(feature=False),
            registry=DEFAULT_REGISTRY,
        )


def test_factory_paddleocr_extension_point_raises_clear_error():
    # PaddleOCR is a declared extension point (8.2) but not implemented.
    with pytest.raises(OcrUnavailableError):
        build_ocr_provider(
            make_settings(provider="docling", fallback="paddleocr"),
            registry=DEFAULT_REGISTRY,
        )
