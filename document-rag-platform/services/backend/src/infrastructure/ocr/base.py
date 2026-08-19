"""OCR provider contract data model (Aşama 8.1).

Implements the ``OcrProvider`` port's return value (``domain.ports.OcrProvider``)
per AKTIF_GOREV.md 8.1:

- ``OcrResult`` carries ``full_text``, ``blocks``, ``confidence``, ``language``,
  ``orientation``, ``preprocessing_steps``, ``engine`` and ``engine_version``.
- Each ``OcrBlock`` carries ``text``, ``bbox``, ``confidence``, ``page_number``
  and ``reading_order`` so citations can be rendered with bounding boxes and an
  OCR text unit can be emitted into the normalized content model (Bölüm 6).

Both dataclasses serialize losslessly via ``to_dict``/``from_dict`` so an OCR
result can be stored as a ``cr_json`` artifact (as required by KKRCN project
rules), mirroring the ``NormalizedSource`` convention.

Low-confidence convention (8.4): an OCR result whose ``confidence`` is at or
below the configured ``OCR_MIN_CONFIDENCE`` threshold should be flagged as
``needs_review`` in the block/unit metadata. We keep the decision as an explicit
method so the config value (e.g. ``OCR_MIN_CONFIDENCE``) stays out of the domain
model and the caller supplies it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Metadata key convention used when an OCR block/unit carries quality signals
# (see 8.4: "OCR confidence düşükse needs_review metadata'sı üret").
NEEDS_REVIEW = "needs_review"
CONFIDENCE = "confidence"


@dataclass
class OcrBlock:
    """A single OCR-detected text region (8.1 block shape).

    ``bbox`` is ``[left, top, width, height]`` in image pixels. ``page_number``
    is 0-based. ``reading_order`` preserves reading sequence.
    """

    text: str
    bbox: Optional[List[float]] = None
    confidence: Optional[float] = None
    page_number: Optional[int] = 0
    reading_order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "bbox": (list(self.bbox) if self.bbox is not None else None),
            "confidence": self.confidence,
            "page_number": self.page_number,
            "reading_order": self.reading_order,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OcrBlock":
        return cls(
            text=data.get("text", ""),
            bbox=data.get("bbox"),
            confidence=data.get("confidence"),
            page_number=data.get("page_number", 0),
            reading_order=data.get("reading_order", 0),
        )


@dataclass
class OcrResult:
    """The ``OcrProvider.extract(...)`` return value (8.1)."""

    full_text: str = ""
    blocks: List[OcrBlock] = field(default_factory=list)
    confidence: Optional[float] = None
    language: Optional[str] = None
    orientation: Optional[int] = 0
    preprocessing_steps: List[str] = field(default_factory=list)
    engine: Optional[str] = None
    engine_version: Optional[str] = None

    # Aggregate confidence = weighted mean of block confidences (weighted by
    # text length so long, empty blocks don't drag it down).
    @property
    def aggregate_confidence(self) -> Optional[float]:
        eligible = [b for b in self.blocks if b.confidence is not None and b.text]
        if not eligible:
            return self.confidence
        total_len = max(1, sum(len(b.text) for b in eligible))
        weighted = sum(
            b.confidence * len(b.text) for b in eligible
        ) / total_len
        return round(weighted, 4)

    def needs_review(self, min_confidence: float = 0.60) -> bool:
        """True when confidence is at/below the threshold (8.4).

        Uses block aggregate confidence when available, else result confidence.
        """
        conf = self.aggregate_confidence
        if conf is None:
            conf = self.confidence
        if conf is None:
            # Unknown quality -> conservatively flag for review.
            return True
        return conf <= min_confidence

    def metadata(self, min_confidence: float = 0.60) -> Dict[str, Any]:
        """Quality metadata to attach to an ``ocr_text`` unit (8.4).

        Always includes ``confidence`` and the ``needs_review`` flag so the
        normalized content / artifact layer has an explicit signal about how
        reliable the extracted text is.
        """
        return {
            CONFIDENCE: self.aggregate_confidence,
            NEEDS_REVIEW: self.needs_review(min_confidence=min_confidence),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "full_text": self.full_text,
            "blocks": [b.to_dict() for b in self.blocks],
            "confidence": self.confidence,
            "aggregate_confidence": self.aggregate_confidence,
            "language": self.language,
            "orientation": self.orientation,
            "preprocessing_steps": list(self.preprocessing_steps),
            "engine": self.engine,
            "engine_version": self.engine_version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OcrResult":
        return cls(
            full_text=data.get("full_text", ""),
            blocks=[OcrBlock.from_dict(b) for b in data.get("blocks") or []],
            confidence=data.get("confidence"),
            language=data.get("language"),
            orientation=data.get("orientation", 0),
            preprocessing_steps=list(data.get("preprocessing_steps") or []),
            engine=data.get("engine"),
            engine_version=data.get("engine_version"),
        )


class OcrError(RuntimeError):
    """Base class for OCR-layer runtime failures."""


class OcrUnavailableError(OcrError):
    """No configured OCR engine is installed/available (clear "OCR unavailable")."""


class OcrConfigurationError(OcrError):
    """The requested provider name is not recognized (bad config)."""


class OcrEngineError(OcrError):
    """An engine ran but failed during extraction."""
