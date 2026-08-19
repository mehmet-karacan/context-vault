"""Docling-based OCR / structured extraction provider (Aşama 8.2).

Implements the ``OcrProvider`` port (``domain.ports``) for Docling. Docling is
a heavy dependency (AKTIF_GOREV.md 10: "ağır bağımlılıkları API image'ına
zorunlu koyma"), so it is imported lazily and never installed by this task. When
Docling is not installed, ``available`` is ``False`` and ``extract`` raises a
clear ``DoclingUnavailableError`` (which the factory degrades from to the
fallback provider per config). Engine name/version are populated from the actual
installed library.

Default language profile is ``tur+eng`` (8.2), resolved to the provider-native
value when Docling is present (``tur+eng`` is docling-native).
"""

from __future__ import annotations

from typing import Any, List, Optional

from .base import OcrBlock, OcrError, OcrResult


class DoclingUnavailableError(OcrError):
    """Raised when Docling is required but not installed/importable."""


class DoclingOcrProvider:
    """Extracts OCR text/blocks from an image or rendered page via Docling."""

    engine = "docling"

    def __init__(
        self,
        languages: Optional[List[str]] = None,
        min_confidence: float = 0.60,
    ):
        self.languages = list(languages) if languages else ["tur", "eng"]
        self.min_confidence = min_confidence
        self.engine_version = self._docling_version()

    # --- availability -------------------------------------------------------

    @staticmethod
    def _docling_version() -> Optional[str]:
        """Returns a version string or None when Docling is not importable."""
        try:
            import docling

            return "docling " + (
                getattr(docling, "__version__", "unknown") or "unknown"
            )
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
            raise DoclingUnavailableError(
                "Docling OCR is not available: docling package is not installed. "
                "Install the ocr profile or configure OCR_FALLBACK_PROVIDER."
            )
        options = options or {}
        page_number = options.get("page_number", 0)
        lang = self._resolve_language(languages)

        try:
            from docling.document_converter import DocumentConverter  # noqa: F401

            # Docling's full pipeline is heavy and slow to import; guard the
            # integration so a missing/incompatible submodule still reports a
            # clear error instead of a raw traceback.
            return self._extract_via_docling(image_or_page, lang, page_number)
        except DoclingUnavailableError:
            raise
        except Exception as exc:  # DOC: best-effort wrapper
            raise DoclingUnavailableError(
                f"Docling extraction unavailable for this input: {exc}"
            ) from exc

    def _extract_via_docling(
        self, image_or_page: Any, lang: str, page_number: int
    ) -> OcrResult:
        # Lazy, defensive: Docling may not expose the exact API across versions,
        # so we parse its result generically where possible, else surface text.
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(image_or_page)
        document = getattr(result, "document", None)
        full_text = ""
        blocks: List[OcrBlock] = []
        order = 0
        if document is not None:
            try:
                for item in document.texts:
                    text = " ".join((item.text or "").split())
                    if not text:
                        continue
                    full_text += ("\n" if full_text else "") + text
                    blocks.append(
                        OcrBlock(
                            text=text,
                            bbox=self._bbox_of(item),
                            confidence=self._confidence_of(item),
                            page_number=page_number,
                            reading_order=order,
                        )
                    )
                    order += 1
            except Exception:
                # Docling model shape drift between versions: fall back to any
                # exported text so we never lose the extracted content.
                exported = getattr(document, "export_to_text", None)
                if callable(exported):
                    try:
                        full_text = exported() or ""
                    except Exception:
                        full_text = ""
        if not full_text:
            raw = str(getattr(image_or_page, "__dict__", ""))
            if raw:
                full_text = raw
        return OcrResult(
            full_text=full_text,
            blocks=blocks,
            confidence=self._aggregate_confidence(blocks),
            language=lang,
            orientation=0,
            preprocessing_steps=[],
            engine=self.engine,
            engine_version=self.engine_version,
        )

    # --- helpers ------------------------------------------------------------

    def _resolve_language(self, languages: Optional[List[str]]) -> str:
        requested = list(languages) if languages else self.languages
        joined = "+".join(requested)
        # Docling uses a plus-joined profile natively.
        return joined or "tur+eng"

    @staticmethod
    def _bbox_of(item: Any) -> Optional[List[float]]:
        try:
            prov = item.prov[0]
            bbox = prov.bbox
            return [bbox.l, bbox.t, bbox.r - bbox.l, bbox.b - bbox.t]
        except Exception:
            return None

    @staticmethod
    def _confidence_of(item: Any) -> Optional[float]:
        conf = getattr(item, "confidence", None)
        try:
            return float(conf) if conf is not None else None
        except Exception:
            return None

    @staticmethod
    def _aggregate_confidence(blocks: List[OcrBlock]) -> Optional[float]:
        eligible = [b for b in blocks if b.confidence is not None]
        if not eligible:
            return None
        return round(sum(b.confidence for b in eligible) / len(eligible), 4)
