"""Image preprocessing tests (Aşama 8.3).

Uses a generated PIL image (solid color + simple shapes, no external fixtures).
Each test is guarded with ``pytest.importorskip("PIL")`` because Pillow is an
optional heavy dependency; when it is absent the suite skips rather than fails
per the task instructions.
"""

import pytest

from src.infrastructure.ocr import (
    binarize,
    denoise,
    detect_rotation,
    fix_exif_orientation,
    normalize_contrast,
    preprocess,
    upscale,
)

pytest.importorskip("PIL")


def make_image(base=(120, 120)):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", base, (200, 200, 200))
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 30, 70, 70], fill=(40, 40, 40))
    return img


def test_binarize_changes_pixel_values():
    img = make_image()
    out = binarize(img)
    assert out is not img
    # Non-white/gray pixel becomes pure black (0) after thresholding.
    pixel = out.convert("L").getpixel((50, 50))
    assert pixel == 0


def test_normalize_contrast_changes_pixels():
    # Build a low-contrast image (only two near-equal gray levels) without any
    # extra dependency so the test stays portable to machines without numpy.
    from PIL import Image, ImageDraw

    low = Image.new("L", (40, 40), 70)
    draw = ImageDraw.Draw(low)
    draw.rectangle([0, 0, 19, 39], fill=80)
    out = normalize_contrast(low)
    assert out is not low
    values = set(out.convert("L").getdata())
    # Autocontrast spreads the narrow 70..80 range across the full scale.
    assert max(values) > 100 and min(values) < 50


def test_upscale_increases_size():
    img = make_image((20, 30))
    out = upscale(img, scale=2)
    assert out.size == (40, 60)


def test_exif_orientation_fix_returns_new_image_or_handles_missing_exif():
    img = make_image()
    out = fix_exif_orientation(img)
    # Without EXIF the image is returned effectively unchanged (still usable).
    assert out.size == img.size


def test_detect_rotation_returns_zero_when_no_exif():
    img = make_image()
    assert detect_rotation(img) == 0


def test_preprocess_records_actual_applied_steps():
    img = make_image((20, 30))
    out, applied = preprocess(img, steps=["upscale", "binarize", "not_a_real_step"])
    assert "not_a_real_step" not in applied
    assert "upscale" in applied
    assert "binarize" in applied
    assert out.size == (40, 60)


def test_denoise_returns_new_image():
    img = make_image()
    out = denoise(img)
    assert out is not img
