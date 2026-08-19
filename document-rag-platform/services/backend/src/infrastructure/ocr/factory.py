"""OCR provider factory (Aşama 8.2).

Selects the OCR engine from config (``OCR_PROVIDER``, ``OCR_FALLBACK_PROVIDER``)
and gate flags (``FEATURE_OCR``, ``OCR_ENABLED``). The chosen provider's engine
is verified for availability; if it is missing the factory degrades to the
fallback provider, and if no configured provider is usable it raises a clear
``OcrUnavailableError``. Unknown provider names raise a clear
``OcrConfigurationError``.

The registry maps provider names to constructors so tests can inject fake
providers (deterministically, with no real binary) and so PaddleOCR can be added
as an extension point (8.2) without touching this module.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Type

from .base import (
    OcrConfigurationError,
    OcrUnavailableError,
)
from .docling_provider import DoclingOcrProvider
from .tesseract_provider import TesseractOcrProvider


class PaddleUnavailableError(OcrUnavailableError):
    """PaddleOCR is declared as an extension point but not implemented yet."""


def _paddle_provider_constructor():
    raise PaddleUnavailableError(
        "PaddleOCR provider is an extension point (Aşama 8.2) and is not "
        "implemented yet; configure OCR_PROVIDER=docling or tesseract."
    )


# Default provider registry. Keys are the accepted OCR_PROVIDER names.
DEFAULT_REGISTRY: Dict[str, Callable[[], object]] = {
    "docling": lambda: DoclingOcrProvider(),
    "tesseract": lambda: TesseractOcrProvider(),
    "paddleocr": _paddle_provider_constructor,
}


def build_ocr_provider(
    settings,
    registry: Optional[Dict[str, Callable[[], object]]] = None,
) -> object:
    """Builds the best available OCR provider for ``settings``.

    Returns an ``OcrProvider`` instance. Raises:
    - ``OcrUnavailableError`` when the feature is disabled or no configured
      provider's engine is available.
    - ``OcrConfigurationError`` when a configured provider name is unknown.
    """
    if not (getattr(settings, "FEATURE_OCR", True) and getattr(settings, "OCR_ENABLED", True)):
        raise OcrUnavailableError(
            "OCR is disabled (FEATURE_OCR/OCR_ENABLED is false); cannot build an "
            "OCR provider."
        )

    reg: Dict[str, Callable[[], object]] = dict(DEFAULT_REGISTRY)
    if registry:
        reg.update(registry)

    primary = getattr(settings, "OCR_PROVIDER", "docling") or "docling"
    fallback = getattr(settings, "OCR_FALLBACK_PROVIDER", "tesseract") or ""
    candidates = [primary]
    if fallback and fallback != primary:
        candidates.append(fallback)

    for name in candidates:
        constructor = reg.get(name)
        if constructor is None:
            raise OcrConfigurationError(
                f"unknown OCR provider: {name!r} (expected one of "
                f"{sorted(reg)})"
            )
        provider = _try_build(constructor)
        if provider is not None and getattr(provider, "available", True):
            return provider

    raise OcrUnavailableError(
        "OCR unavailable: no configured provider engine is available (tried "
        + ", ".join(repr(c) for c in candidates)
        + "). Install an ocr profile dependency or fix OCR_PROVIDER/"
        "OCR_FALLBACK_PROVIDER."
    )


def _try_build(constructor: Callable[[], object]):
    try:
        return constructor()
    except Exception:
        return None
