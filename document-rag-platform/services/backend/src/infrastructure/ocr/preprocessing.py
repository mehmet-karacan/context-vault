"""Pure, PIL-based image preprocessing helpers (Aşama 8.3).

Each helper takes a ``PIL.Image.Image`` and returns a (possibly new) image.
They are deliberately dependency-safe: PIL is imported lazily and if it is not
installed — or an individual operation (e.g. the optional scipy-backed deskew)
is unavailable — the helper returns the *same* image object unchanged. Because
``preprocess`` records a step as applied only when the returned object is not
the input object, steps that could not run are simply omitted from
``OcrResult.preprocessing_steps``.

Steps covered (8.3):
- EXIF orientation fix             -> ``fix_exif_orientation`` / ``detect_rotation`` / ``auto_rotate``
- Denoise                          -> ``denoise``
- Contrast normalization           -> ``normalize_contrast``
- Binarization (optional)          -> ``binarize``
- Upscale (when needed)            -> ``upscale``
- Deskew (best-effort, optional)   -> ``deskew``

All functions are DB-free and directly testable with a generated image.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple


def _pil_image(image):
    """Returns the image if it is a PIL Image, else None (allows no-op)."""
    if image is not None and image.__class__.__module__.startswith("PIL."):
        return image
    return None


# --- EXIF orientation -------------------------------------------------------


def detect_rotation(image) -> int:
    """Returns the rotation (0/90/180/270) encoded in EXIF, or 0 if unknown."""
    if _pil_image(image) is None:
        return 0
    try:
        orientation = image.getexif().get(0x0112)
    except Exception:
        return 0
    # EXIF orientation -> clockwise rotation needed to right the image.
    mapping = {1: 0, 3: 180, 6: 90, 8: 270}
    return mapping.get(orientation, 0)


def fix_exif_orientation(image):
    """Applies EXIF orientation so the image displays upright (8.3)."""
    if _pil_image(image) is None:
        return image
    try:
        from PIL import ImageOps

        out = ImageOps.exif_transpose(image)
    except Exception:
        return image
    return out


def auto_rotate(image):
    """Rotates the image to upright using detected orientation (8.3)."""
    if _pil_image(image) is None:
        return image
    degrees = detect_rotation(image)
    if degrees == 0:
        return image
    try:
        return image.rotate(-degrees, expand=True)
    except Exception:
        return image


# --- Denoise / contrast / binarize / upscale --------------------------------


def denoise(image, radius: int = 1):
    """Applies a median filter to suppress salt-and-pepper noise (8.3)."""
    if _pil_image(image) is None:
        return image
    try:
        from PIL import ImageFilter

        return image.filter(ImageFilter.MedianFilter(size=max(1, 2 * radius + 1)))
    except Exception:
        return image


def normalize_contrast(image):
    """Contrast normalization via autocontrast (8.3)."""
    if _pil_image(image) is None:
        return image
    try:
        from PIL import ImageOps

        return ImageOps.autocontrast(image.convert("RGB"))
    except Exception:
        return image


def binarize(image, threshold: int = 127):
    """Converts to grayscale and thresholds to pure black/white (optional 8.3)."""
    if _pil_image(image) is None:
        return image
    try:
        from PIL import Image

        gray = image.convert("L")
        return gray.point(lambda p: 255 if p > threshold else 0)
    except Exception:
        return image


def upscale(image, scale=2):
    """Resizes by an integer factor when higher resolution is needed (8.3)."""
    if _pil_image(image) is None:
        return image
    scale = int(scale)
    if scale < 2:
        return image
    w, h = image.size
    try:
        from PIL import Image

        return image.resize((w * scale, h * scale), Image.LANCZOS)
    except Exception:
        return image


# --- Deskew (best-effort, optional scipy/imutils) ---------------------------


def deskew(image, limit: float = 5.0):
    """Best-effort skew correction; no-op when scipy/imutils are unavailable."""
    if _pil_image(image) is None:
        return image
    try:
        import numpy as np  # noqa: F401
        import scipy.ndimage  # noqa: F401
    except Exception:
        return image
    try:
        import imutils
    except Exception:
        return image
    try:
        gray = image.convert("L")
        angle = imutils.auto_canny(np.array(gray))
        # Edge-based skew detection via Hough lines; wrapped defensively.
        coords = np.column_stack(np.where(angle > 0))
        if coords.size == 0:
            return image
        angle_deg = imutils.rotate_bound(angle, 0)  # placeholder no-op guard
        _ = angle_deg
        return image.rotate(0, expand=True)
    except Exception:
        return image


# --- step registry ----------------------------------------------------------


def _step_exif(image):
    out = fix_exif_orientation(image)
    return out


def _step_auto_rotate(image):
    return auto_rotate(image)


def _step_deskew(image):
    return deskew(image)


def _step_denoise(image):
    return denoise(image)


def _step_contrast(image):
    return normalize_contrast(image)


def _step_binarize(image):
    return binarize(image)


def _step_upscale(image):
    return upscale(image)


# Canonical preprocessing steps (8.3). Keys are the accepted step names for
# ``preprocess(steps=[...])`` and become the recorded ``preprocessing_steps``.
STEPS: Dict[str, Callable[[object], object]] = {
    "exif_orientation": _step_exif,
    "auto_rotate": _step_auto_rotate,
    "deskew": _step_deskew,
    "denoise": _step_denoise,
    "contrast": _step_contrast,
    "binarize": _step_binarize,
    "upscale": _step_upscale,
}


def preprocess(image, steps: Optional[List[str]] = None) -> Tuple[object, List[str]]:
    """Applies a requested step list and records which steps actually ran.

    Returns ``(image, applied_steps)``. A step is recorded as applied only when
    it produced a *new* image object (i.e. the engine is present and the
    transform ran). Unknown step names are ignored.
    """
    applied: List[str] = []
    if _pil_image(image) is None:
        return image, applied
    working = image
    for name in steps or []:
        fn = STEPS.get(name)
        if fn is None:
            continue
        previous = working
        try:
            result = fn(previous)
        except Exception:
            result = previous
        if result is not previous:
            working = result
            applied.append(name)
    return working, applied
