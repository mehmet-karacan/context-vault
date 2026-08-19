"""OCR infrastructure package (Aşama 8).

Exposes the OCR contract dataclasses (``OcrResult`` / ``OcrBlock``), the
Docling and Tesseract providers, the preprocessing helpers and the factory
(``build_ocr_provider``) that selects a provider from config and safely
degrades to the configured fallback when an engine is missing.

See AKTIF_GOREV.md Aşama 8 for the provider contract (§8.1), provider options
(§8.2) and image preprocessing steps (§8.3).
"""

from __future__ import annotations

from .base import (
    CONFIDENCE,
    NEEDS_REVIEW,
    OcrBlock,
    OcrConfigurationError,
    OcrEngineError,
    OcrError,
    OcrResult,
    OcrUnavailableError,
)
from .docling_provider import DoclingOcrProvider, DoclingUnavailableError
from .factory import (
    DEFAULT_REGISTRY,
    PaddleUnavailableError,
    build_ocr_provider,
)
from .preprocessing import (
    STEPS,
    auto_rotate,
    binarize,
    denoise,
    deskew,
    detect_rotation,
    fix_exif_orientation,
    normalize_contrast,
    preprocess,
    upscale,
)
from .tesseract_provider import TesseractOcrProvider, TesseractUnavailableError

__all__ = [
    "OcrResult",
    "OcrBlock",
    "CONFIDENCE",
    "NEEDS_REVIEW",
    "OcrError",
    "OcrUnavailableError",
    "OcrConfigurationError",
    "OcrEngineError",
    "DoclingOcrProvider",
    "DoclingUnavailableError",
    "TesseractOcrProvider",
    "TesseractUnavailableError",
    "PaddleUnavailableError",
    "build_ocr_provider",
    "DEFAULT_REGISTRY",
    "preprocess",
    "STEPS",
    "fix_exif_orientation",
    "detect_rotation",
    "auto_rotate",
    "deskew",
    "denoise",
    "normalize_contrast",
    "binarize",
    "upscale",
]
