# ADR-003 — Versioned Ingestion & Immutable Artifacts

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Context Vault platform implementation (Aşama 2 / 7.6)

## Context

Uploads, repository commits and re-indexes change document content repeatedly.
Overwriting a live document inline makes rollback impossible and risks serving
half-built state. Re-index must work from previously stored artifacts without a
fresh upload (`AKTIF_GOREV.md` Aşama 2 & §13).

## Decision

- Every ingestion result is a new immutable `DocumentVersion`
  (`services/backend/src/models.py` — `document_versions`, unique on
  `(document_id, version_no)`).
  - `documents.active_version_id` (FK to `document_versions.id`) is only
    swapped once the whole new version is fully ready and built. Nothing reads
    a half-built version (`models.py` `Document`, `DocumentVersion`).
  - `DocumentArtifact`, `SourceFile`, `Chunk`, `ChunkEmbedding`,
    `EmbeddingProfile`, `IngestionJob`/`IngestionEvent` also live in
    `models.py`; chunks are linked to a `version_id`.
- Object storage keys are immutable per version and centralized in
  `services/backend/src/infrastructure/storage/object_keys.py`, following the
  standard from `AKTIF_GOREV.md §8` / Aşama 2 "Object key standardı":
  `projects/{p}/documents/{d}/versions/{v}/original/{safe_filename}`,
  `.../normalized/document.json`, `.../normalized/document.md`, `.../artifacts/...`
  (`original_key`, `normalized_json_key`, `normalized_markdown_key`,
  `artifact_key`; `safe_filename` guards traversal/control/length).
- Re-index strategy (`application/reindex_service.py`, Aşama 7.6):
  - snapshot keyed by `source_revision`; only files whose `content_hash`
    changed are re-parsed/re-chunked/re-embedded; unchanged files copy the
    previous version's chunks; deleted files simply never appear in the new
    version; `active_version_id` swap is the final atomic step.
- Object storage I/O is via `infrastructure/storage/minio_storage.py`.

## Consequences

- Safe rollback: the previously active version remains fully usable until the
  new version is ready.
- No clobber: immutable keys + content hashes prevent silent overwrites.
- Re-index runs without re-uploading the original file (from stored
  normalized JSON / original artifacts).
