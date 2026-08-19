"""Tesseract (pytesseract) local fallback provider (Aşama 8.2).

Implements the ``OcrProvider`` port for the local Tesseract engine. pytesseract
and the tesseract binary are both optional (heavy) dependencies, so they are
imported lazily. When either is missing, ``available`` is ``False`` and
``extract`` raises a clear ``TesseractUnavailableError`` (the factory degrades
to the configured fallback / reports OCR unavailable).

Extraction runs ``pytesseract.image_to_data`` and maps per-block results into
``OcrBlock`` with text/bbox/confidence and preserves reading order. The engine
name is ``"tesseract"`` and ``engine_version`` comes from
``pytesseract.get_tesseract_version()``.

Default language profile is ``tur+eng`` (8.2), resolved to the tesseract
language code (``tur+eng``); when ``tur`` is not supported by the local binary
it degrades to ``eng`` alone.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .base import OcrBlock, OcrError, OcrResult


class TesseractUnavailableError(OcrError):
    """Raised when pytesseract or the tesseract binary is missing."""


class TesseractOcrProvider:
    """Local Tesseract OCR adapter via pytesseract."""

    engine = "tesseract"

    def __init__(
        self,
        languages: Optional[List[str]] = None,
        min_confidence: float = 0.60,
        preprocessing_steps: Optional[List[str]] = None,
    ):
        self.languages = list(languages) if languages else ["tur", "eng"]
        self.min_confidence = min_confidence
        self.preprocessing_steps = list(preprocessing_steps) if preprocessing_steps else []
        self.engine_version = self._tesseract_version()

    # --- availability -------------------------------------------------------

    @staticmethod
    def _tesseract_version() -> Optional[str]:
        """Returns tesseract version string or None when binary/pytesseract is missing."""
        try:
            import pytesseract

            version = pytesseract.get_tesseract_version()
            return "tesseract " + str(version)
        except Exception:
            return None

    @property
    def available(self) -> bool:
        return self.engine_version is not None

    # --- contract -----------------------------------------------------------

    def extract(
        self,
        image_or_page: Any,
        languages: Optional[List[str]] = None,
        options: Optional[dict] = None,
    ) -> OcrResult:
        if not self.available:
            raise TesseractUnavailableError(
                "Tesseract OCR is not available: pytesseract package or the "
                "tesseract binary is missing. Install the ocr profile or configure "
                "a different OCR provider."
            )
        try:
            import pytesseract
        except Exception as exc:  # pragma: no cover - guarded by availability
            raise TesseractUnavailableError(str(exc)) from exc

        options = options or {}
        page_number = options.get("page_number", 0)
        lang = self._resolve_language(languages)
        applied_steps = list(self.preprocessing_steps)

        try:
            data = pytesseract.image_to_data(
                image_or_page, lang=lang, output_type=pytesseract.Output.DICT
            )
        except Exception as exc:
            # e.g. "tur" unsupported -> retry with eng only.
            if "+" in lang and "tur" in lang.split("+"):
                try:
                    data = pytesseract.image_to_data(
                        image_or_page,
                        lang="eng",
                        output_type=pytesseract.Output.DICT,
                    )
                    lang = "eng"
                except Exception as exc2:
                    raise TesseractUnavailableError(
                        f"tesseract failed to run OCR: {exc2}"
                    ) from exc2
            else:
                raise TesseractUnavailableError(
                    f"tesseract failed to run OCR: {exc}"
                ) from exc

        text_data = data.get("text", [])
        conf_data = data.get("conf", [])
        left = data.get("left", [])
        top = data.get("top", [])
        width = data.get("width", [])
        height = data.get("height", [])
        block_nums = data.get("block_num", [])

        blocks = self._build_blocks(
            text_data=text_data,
            conf_data=conf_data,
            left=left,
            top=top,
            width=width,
            height=height,
            block_nums=block_nums,
            page_number=page_number,
        )
        full_text = "\n".join(b.text for b in blocks if b.text)
        conf = self._aggregate_confidence(blocks)
        return OcrResult(
            full_text=full_text,
            blocks=blocks,
            confidence=conf,
            language=lang,
            orientation=0,
            preprocessing_steps=applied_steps,
            engine=self.engine,
            engine_version=self.engine_version,
        )

    # --- helpers ------------------------------------------------------------

    def _resolve_language(self, languages: Optional[List[str]]) -> str:
        requested = list(languages) if languages else self.languages
        joined = "+".join(requested)
        return joined or "tur+eng"

    @staticmethod
    def _build_blocks(
        text_data: List[str],
        conf_data: List[Any],
        left: List[Any],
        top: List[Any],
        width: List[Any],
        height: List[Any],
        block_nums: List[Any],
        page_number: int,
    ) -> List[OcrBlock]:
        """Groups tesseract tokens into blocks by ``block_num`` (reading order)."""
        grouped: dict = {}
        order_by_block: dict = {}
        for i, text in enumerate(text_data):
            cleaned = (text or "").strip()
            if not cleaned:
                continue
            blk = block_nums[i] if i < len(block_nums) else 0
            blob = grouped.setdefault(
                blk, {"words": [], "conf": [], "bbox": None, "order": None}
            )
            blob["words"].append(cleaned)
            try:
                blob["conf"].append(float(conf_data[i]))
            except (TypeError, ValueError):
                blob["conf"].append(-1.0)
            bbox = _to_bbox(left, top, width, height, i)
            if bbox is not None:
                if blob["bbox"] is None:
                    blob["bbox"] = list(bbox)
                else:
                    cur = blob["bbox"]
                    cur[0] = min(cur[0], bbox[0])
                    cur[1] = min(cur[1], bbox[1])
                    cur[2] = max(cur[0] + cur[2], bbox[0] + bbox[2]) - cur[0]
                    cur[3] = max(cur[1] + cur[3], bbox[1] + bbox[3]) - cur[1]
            if blob["order"] is None:
                blob["order"] = i

        ordered = sorted(grouped.items(), key=lambda kv: kv[1]["order"])
        blocks: List[OcrBlock] = []
        reading = 0
        for _blk, blob in ordered:
            if not blob["words"]:
                continue
            confs = [c for c in blob["conf"] if c >= 0]
            conf = round(sum(confs) / len(confs), 4) if confs else None
            blocks.append(
                OcrBlock(
                    text=" ".join(blob["words"]),
                    bbox=blob["bbox"],
                    confidence=conf,
                    page_number=page_number,
                    reading_order=reading,
                )
            )
            reading += 1
        return blocks

    @staticmethod
    def _aggregate_confidence(blocks: List[OcrBlock]) -> Optional[float]:
        eligible = [b for b in blocks if b.confidence is not None]
        if not eligible:
            return None
        total_len = max(1, sum(len(b.text) for b in eligible))
        return round(
            sum(b.confidence * len(b.text) for b in eligible) / total_len, 4
        )


def _to_bbox(left, top, width, height, i):
    """Builds [l, t, w, h] for token *i*, or None when data is missing."""
    try:
        return [
            float(left[i]),
            float(top[i]),
            float(width[i]),
            float(height[i]),
        ]
    except (IndexError, TypeError, ValueError):
        return None
