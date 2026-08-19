"""Image parser + OCR routing of a PNG/JPEG/TIFF/... file (Aşama 8.4).

Implements the ``DocumentParser`` port for raster images. An image has no
embedded text, so parsing always goes through an OCR provider, producing a
normalized ``NormalizedSource`` whose searchable content lives in
``ocr_text`` content units (AKTIF_GOREV.md §8.4 / Bölüm 6).

Mapping to the normalized-content pipeline:

- a ``file_header`` unit records the file path, MIME/detected format and
  (when Pillow is available) the pixel dimensions;
- a successful OCR run emits one ``ocr_text`` unit carrying the full
  recognized text plus, per recognized block, an additional ``ocr_text``
  unit whose ``SourceLocator.bbox`` is the block's bounding box — so the UI
  can render bounding-box citations (kabul kriteri: "bounding-box citation'i
  UI'da gosterilebilecek bicimde sakla");
- when the OCR provider is **unavailable** or the run raises, we never crash
  the parse: an ``image`` unit with a ``needs_review`` warning in metadata is
  emitted instead of silently dropping the document;
- when the recognized confidence is below the configured minimum, the source
  metadata records ``needs_review=True`` (kabul kriteri: "OCR confidence
  dusukse needs_review metadata'si uret").

OCRS result / OCR provider contract
------------------------------------
``infra/ocr/`` (OcrResult / OcrBlock, providers, preprocessing) is being built
in parallel; it is not present at authoring time. This parser therefore codes
against the *documented* contract (``OcrBlock`` = text/confidence/bbox/page and
``OcrResult`` = full_text/blocks/confidence/orientation/engine/preprocessing)
and normalizes whatever duck-typed result the injected provider returns via
:func:`_coerce_ocr_result`. When ``infra/ocr`` lands with a matching shape this
parser continues to work unchanged; the provider can also be injected for
tests.

Extension point (§8.5)
----------------------
The data model already supports ``image_caption`` / ``chart_data`` /
``diagram_description`` units for a future ``VisionDescriptionProvider``. No
such provider is required now; if an OCR result (or parse ``options``) carries
any of those blobs they are surfaced as metadata / optional caption units,
keeping the pipeline forward-compatible.
"""

from __future__ import annotations

import mimetypes
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...config import settings
from ...domain.normalized_content import (
    ContentUnit,
    Hierarchy,
    NormalizedSource,
    SourceLocator,
    UnitType,
)
from ...domain.ports import DocumentParser
from .ocr_routing import OcrRoutingConfig, should_ocr

_IMAGE_SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")

_PARSER_NAME = "image"
_PARSER_VERSION = "0.1.0"


# --- OcrResult / OcrBlock contract (documented shape, see module docstring) -


@dataclass
class OcrBlock:
    """A single recognized text region with its spatial box (documented)."""

    text: str = ""
    confidence: Optional[float] = None
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1] in page/image coords
    page: Optional[int] = None
    block_type: Optional[str] = None


@dataclass
class OcrResult:
    """The normalized OCR output a provider returns (documented)."""

    full_text: str = ""
    blocks: List[OcrBlock] = field(default_factory=list)
    confidence: Optional[float] = None
    orientation: Optional[Dict[str, Any]] = None
    engine: Optional[str] = None
    preprocessing_steps: List[str] = field(default_factory=list)


class ImageParser(DocumentParser):
    """Parses a raster image into OCR-backed normalized content (Aşama 8.4).

    ``provider`` is an optional ``OcrProvider``-conforming object with an
    ``extract(image, languages, options)`` method returning an ``OcrResult``.
    When ``None``, a provider is resolved lazily from ``infra/ocr`` (factory)
    if available; when none can be resolved the parse still succeeds and emits
    an ``image`` unit flagged ``needs_review`` (no crash). This makes every
    OCR dependency injectable and the parser DB-free and unit-testable.
    """

    source_type = "image"

    def __init__(
        self,
        provider: Optional[Any] = None,
        min_confidence: Optional[float] = None,
        config: Optional[OcrRoutingConfig] = None,
    ):
        self._provider = provider
        self._min_confidence = (
            min_confidence
            if min_confidence is not None
            else getattr(settings, "OCR_MIN_CONFIDENCE", 0.5)
        )
        self._config = config if config is not None else OcrRoutingConfig()

    def supports(self, mime_type: str, extension: str) -> bool:
        ext = (extension or "").lower().lstrip(".")
        if ext in tuple(e.lstrip(".") for e in _IMAGE_SUPPORTED_EXTS):
            return True
        mime = (mime_type or "").split(";", 1)[0].strip().lower()
        return mime.startswith("image/")

    # --- parse -----------------------------------------------------------

    def parse(
        self,
        file_path: str,
        filename: str,
        options: Optional[dict] = None,
    ) -> NormalizedSource:
        options = options or {}
        mime_type = options.get("mime_type") or self._guess_mime(filename)

        source = NormalizedSource(
            source_id=str(uuid.uuid4()),
            source_type=self.source_type,
            title=filename,
            language=self._language_from_mime(mime_type),
        )
        source.metadata = {
            "parser": _PARSER_NAME,
            "parser_version": _PARSER_VERSION,
            "origin": filename,
            "mime_type": mime_type,
        }

        file_header = self._build_file_header(filename, file_path, mime_type)
        source.units.append(file_header)
        unit_order = [0]

        if not should_ocr(self.source_type, config=self._config):
            # Feature disabled: record an unsearchable image placeholder with a
            # review warning rather than emitting OCR content.
            source.metadata["needs_review"] = True
            source.metadata["ocr"] = False
            source.units.append(
                self._build_image_unit(
                    filename,
                    options,
                    order=unit_order[0] + 1,
                    reason="OCR feature disabled",
                )
            )
            return source

        self._extend_with_vision_description(source, options)

        result, error = self._run_ocr(file_path, source, options)
        if result is None:
            # Provider unavailable / failed -> emit an image unit with a
            # needs_review warning instead of failing the whole parse.
            source.metadata["needs_review"] = True
            source.metadata["ocr"] = False
            source.metadata["ocr_error"] = error
            source.units.append(
                self._build_image_unit(
                    filename,
                    options,
                    order=unit_order[0] + 1,
                    reason=f"OCR unavailable: {error}",
                )
            )
            return source

        source.metadata["ocr"] = True
        source.metadata["ocr_engine"] = result.engine or "unknown"
        source.metadata["ocr_confidence"] = result.confidence
        source.metadata["orientation"] = result.orientation or {}
        source.metadata["preprocessing_steps"] = list(result.preprocessing_steps or [])

        needs_review = (
            result.confidence is None
            or result.confidence < self._min_confidence
        )
        source.metadata["needs_review"] = needs_review
        if needs_review:
            source.metadata["review_reason"] = (
                "low OCR confidence"
                if result.confidence is not None
                else "missing OCR confidence"
            )

        self._emit_ocr_units(source, result, filename, unit_order)
        return source

    # --- OCR execution ----------------------------------------------------

    def _run_ocr(
        self,
        file_path: str,
        source: NormalizedSource,
        options: Dict[str, Any],
    ) -> tuple[Optional[OcrResult], Optional[str]]:
        provider = self._provider or self._resolve_provider()
        if provider is None:
            return None, "no OCR provider available"
        languages = [source.language] if source.language else []
        try:
            raw = provider.extract(file_path, languages=languages, options=options or None)
        except Exception as exc:  # noqa: BLE001 - provider failure must not crash parse
            return None, f"{type(exc).__name__}: {exc}"
        try:
            return self._coerce_ocr_result(raw), None
        except Exception as exc:  # malformed result shape
            return None, f"invalid OCR result: {type(exc).__name__}: {exc}"

    def _resolve_provider(self) -> Optional[Any]:
        """Lazily builds an OCR provider from ``infra/ocr``'s factory.

        Uses ``build_ocr_provider(settings)`` (infra/ocr Aşama 8.2). When OCR is
        disabled or no configured engine is available the factory raises
        ``OcrUnavailableError`` / ``OcrConfigurationError``; we swallow those and
        return ``None`` so the parse degrades gracefully to the ``needs_review``
        image-unit fallback instead of crashing.
        """
        try:
            from ..ocr.factory import build_ocr_provider  # type: ignore
        except Exception:  # infra/ocr not importable/not built
            return None
        try:
            return build_ocr_provider(settings)
        except Exception:  # OCR disabled or no provider engine available
            return None

    @staticmethod
    def _coerce_ocr_result(raw: Any) -> OcrResult:
        """Normalizes a provider result (documented OcrResult or duck-typed)
        into our local ``OcrResult`` contract."""
        if isinstance(raw, OcrResult):
            return raw
        result = OcrResult(
            full_text=getattr(raw, "full_text", "") or getattr(raw, "text", "") or "",
            confidence=getattr(raw, "confidence", None),
            orientation=getattr(raw, "orientation", None) or {},
            engine=getattr(raw, "engine", None),
            preprocessing_steps=list(getattr(raw, "preprocessing_steps", []) or []),
        )
        for block in getattr(raw, "blocks", []) or []:
            if isinstance(block, OcrBlock):
                result.blocks.append(block)
            else:
                result.blocks.append(
                    OcrBlock(
                        text=getattr(block, "text", "") or "",
                        confidence=getattr(block, "confidence", None),
                        bbox=getattr(block, "bbox", None),
                        page=getattr(block, "page", None),
                        block_type=getattr(block, "block_type", None),
                    )
                )
        return result

    # --- unit builders -----------------------------------------------------

    def _build_file_header(
        self, filename: str, file_path: str, mime_type: str
    ) -> ContentUnit:
        dims = _read_dimensions(file_path)
        metadata: Dict[str, Any] = {"language": "image", "mime_type": mime_type}
        if dims is not None:
            metadata["width"], metadata["height"] = dims
        return ContentUnit(
            unit_id=f"{_PARSER_NAME}:file_header",
            unit_type=UnitType.FILE_HEADER,
            text="",
            markdown=None,
            order=0,
            hierarchy=Hierarchy(heading_path=[], depth=0),
            locator=SourceLocator(file_path=filename, page_start=0, page_end=0),
            metadata=metadata,
        )

    def _build_image_unit(
        self,
        filename: str,
        options: Dict[str, Any],
        *,
        order: int,
        reason: str,
    ) -> ContentUnit:
        return ContentUnit(
            unit_id=f"{_PARSER_NAME}:image",
            unit_type=UnitType.IMAGE,
            text="",
            markdown=None,
            order=order,
            hierarchy=Hierarchy(heading_path=[], depth=0),
            locator=SourceLocator(file_path=filename, page_start=0, page_end=0),
            metadata={
                "needs_review": True,
                "review_reason": reason,
                "caption": options.get("caption"),
            },
        )

    def _emit_ocr_units(
        self,
        source: NormalizedSource,
        result: OcrResult,
        filename: str,
        order: List[int],
    ) -> None:
        """Emits the full-text ``ocr_text`` unit plus one per recognized block."""
        full_bbox = _overall_bbox(result.blocks)

        order[0] += 1
        full_unit_id = f"{_PARSER_NAME}:ocr"
        source.units.append(
            ContentUnit(
                unit_id=full_unit_id,
                unit_type=UnitType.OCR_TEXT,
                text=result.full_text,
                markdown=result.full_text,
                order=order[0],
                hierarchy=Hierarchy(heading_path=[], depth=0),
                locator=SourceLocator(
                    file_path=filename,
                    page_start=0,
                    page_end=0,
                    bbox=full_bbox,
                ),
                metadata={
                    "confidence": result.confidence,
                    "engine": result.engine,
                    "orientation": result.orientation or {},
                    "preprocessing_steps": list(result.preprocessing_steps or []),
                },
            )
        )

        for i, block in enumerate(result.blocks, start=1):
            if not (block.text or "").strip() and not block.bbox:
                continue
            order[0] += 1
            source.units.append(
                ContentUnit(
                    unit_id=f"{_PARSER_NAME}:ocr:block:{i}",
                    unit_type=UnitType.OCR_TEXT,
                    text=block.text,
                    markdown=block.text,
                    order=order[0],
                    hierarchy=Hierarchy(
                        heading_path=[], depth=1, parent_unit_id=full_unit_id
                    ),
                    locator=SourceLocator(
                        file_path=filename,
                        page_start=block.page if block.page is not None else 0,
                        page_end=block.page if block.page is not None else 0,
                        bbox=block.bbox,
                        block_index=i,
                    ),
                    metadata={
                        "confidence": block.confidence,
                        "block_type": block.block_type,
                    },
                )
            )

    def _extend_with_vision_description(
        self, source: NormalizedSource, options: Dict[str, Any]
    ) -> None:
        """§8.5 extension point: surface caption/chart/diagram blobs if present.

        No ``VisionDescriptionProvider`` is required; when ``options`` (or a
        future provider result) supplies ``caption`` / ``chart_data`` /
        ``diagram_description`` they are recorded on the source metadata so the
        data model already accommodates them without breaking parsing.
        """
        for key in ("image_caption", "chart_data", "diagram_description"):
            if options.get(key):
                source.metadata[key] = options.get(key)

    # --- small helpers -----------------------------------------------------

    @staticmethod
    def _guess_mime(filename: str) -> str:
        mime, _enc = mimetypes.guess_type(filename)
        return mime or "application/octet-stream"

    @staticmethod
    def _language_from_mime(mime_type: str) -> Optional[str]:
        if mime_type.startswith("image/"):
            return "image"
        return None


def _read_dimensions(file_path: str) -> Optional[tuple[int, int]]:
    """Returns ``(width, height)`` when Pillow is available, else ``None``."""
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    try:
        with Image.open(file_path) as im:
            return im.width, im.height
    except Exception:
        return None


# Bounding-box convention: every bbox is ``[left, top, width, height]`` in image
# pixels, matching ``infra.ocr.base.OcrBlock`` (Aşama 8.1). `_overall_bbox` and
# the coercion below MUST keep this convention — do not regress to ``[x0, y0,
# x1, y1]`` (right/bottom) semantics.
BBOX_FORMAT = "[left, top, width, height]"


def _overall_bbox(blocks: List[OcrBlock]) -> Optional[List[float]]:
    """Combines per-block boxes (``[l, t, w, h]``) into one overall bbox.

    Returns ``[min_left, min_top, total_width, total_height]`` computed from the
    union of all block rectangles, still in ``[left, top, width, height]`` form
    (see ``BBOX_FORMAT``).
    """
    boxes = [b.bbox for b in blocks if b.bbox is not None]
    if not boxes:
        return None
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    max_right = max(b[0] + b[2] for b in boxes)
    max_bottom = max(b[1] + b[3] for b in boxes)
    return [left, top, max_right - left, max_bottom - top]
