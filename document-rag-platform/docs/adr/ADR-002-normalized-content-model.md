# ADR-002 — Normalized Content Model

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Context Vault platform implementation (Aşama 3)

## Context

DOCX, PDF, plain text/Markdown, source code and raster images each produce
different raw structures. If parsers emitted raw chunks directly, the chunker /
embedding / retrieval pipeline could not consume a uniform structure, and
re-index / re-parse would be impossible without the source file
(`AKTIF_GOREV.md` Bölüm 6).

## Decision

- Every parser produces the shared normalized model first, **never raw chunks**:
  - `NormalizedSource`, `ContentUnit`, `SourceLocator`, `Hierarchy` and the
    `UnitType` enum in `services/backend/src/domain/normalized_content.py`
    (`UnitType` values: `heading`, `paragraph`, `list_item`, `table`, `code`,
    `formula`, `image`, `image_caption`, `ocr_text`, `page_break`,
    `file_header`, `symbol`, `configuration`).
- The model is JSON round-trippable and lossless: each class exposes
  `to_dict()` / `from_dict()`; `UnitType` enum values are reduced to their
  string values for JSON (`domain/normalized_content.py`).
- Unit classes are dataclasses whose non-info fields default to `None` / empty,
  so every parser fills only what it knows and the rest stays `null` / `[]`.
- `source_id` is unique per parse call; `version_id` is assigned externally by
  the ingestion/version layer.

## Implementation notes (real parser set)

The `DocumentParser` port (`domain/ports.py`) is implemented by routers and
parsers under `infrastructure/parsers/`:

- `router.py` — MIME/extension/magic-byte routing;
- `docx_parser.py`, `pdf_parser.py`, `docling_parser.py`, `image_parser.py`,
  `code_parser.py` — per-source parsers;
- plain `.txt` / `.md` route through the plain-text path.

## Consequences

- One ingestion pipeline for documents, images and code; chunkers and
  embeddings consume only `NormalizedSource`.
- Lossless JSON/Markdown artifacts can be stored and re-indexed without the
  original file (see ADR-003).
- Richer citation metadata (page, bbox, heading path, line range) is carried
  end-to-end via `SourceLocator` / `Hierarchy`.
