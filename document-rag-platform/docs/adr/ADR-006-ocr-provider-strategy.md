# ADR-006 — OCR Provider Strategy

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Context Vault platform implementation (Aşama 8)

## Context

Scanned PDFs and raster images (PNG/JPEG/TIFF) carry no extractable text and
need OCR. Multiple engines exist with different trade-offs (layout fidelity vs.
speed/language models), and an OCR feature must be cleanly reversible and
extensible without rewriting the parser pipeline (`AKTIF_GOREV.md §8`).

## Decision

- Use a provider contract plus a **primary / fallback** factory:
  - Contract: `infrastructure/ocr/base.py` (`OcrProvider`, `OcrResult`,
    `OcrBlock`, `OcrUnavailableError`, `OcrConfigurationError`).
  - Providers: `infrastructure/ocr/docling_provider.py`
    (`DoclingOcrProvider` — primary), `infrastructure/ocr/tesseract_provider.py`
    (`TesseractOcrProvider` — fallback). Preprocessing lives in
    `infrastructure/ocr/preprocessing.py`.
  - Factory: `infrastructure/ocr/factory.py`
    (`build_ocr_provider`, `DEFAULT_REGISTRY`, `OCR_PROVIDER`,
    `OCR_FALLBACK_PROVIDER`). When the selected provider's engine is missing it
    degrades to the fallback; if none is usable it raises `OcrUnavailableError`.
  - Feature gating: `FEATURE_OCR` / `OCR_ENABLED` in `config.py`
    (defaults `True` / `True`); the factory refuses to build when disabled.
- **Routing** — `infrastructure/parsers/ocr_routing.py`
  (`OcrRoutingConfig`, `should_ocr`, `route_pdf_pages_to_ocr`):
  - `image` source type → always OCR;
  - digital (text coverage ≥ 0.75) → no OCR; scanned/low-coverage (≤ 0.25) →
    OCR; missing coverage defaults to OCR (safe bias);
  - `infrastructure/parsers/image_parser.py` (`ImageParser` of the
    `DocumentParser` port) parses PNG/JPEG/TIFF by routing through OCR.
- **Scanned vs digital** uses the text-coverage classification already recorded
  by `infrastructure/parsers/pdf_parser.py`
  (`classification` / `text_coverage` / `pages_needing_ocr`).
- **Low confidence → human review** — `image_parser.py` sets
  `needs_review=True` in source metadata when OCR confidence is below
  `OCR_MIN_CONFIDENCE` (`config.py`, 0.5) or missing; it never crashes the
  parse if the provider is unavailable (emits an `image` unit flagged
  `needs_review` instead).
- **Extension point** — `factory.DEFAULT_REGISTRY` already reserves `paddleocr`
  (`PaddleUnavailableError` = declared but not yet implemented), so PaddleOCR /
  future engines can be added without touching this module (Aşama 8.2).

## Consequences

- A primary engine (Docling) with a working fallback (Tesseract) gives
  availability without operator intervention.
- The feature is safely gated behind `FEATURE_OCR` for rollback, and provider
  choice is config-driven.
- Low-confidence OCR output is explicitly surfaced for human review rather than
  silently indexed.
- Adding new OCR engines later needs no pipeline changes (registry extension
  point).
