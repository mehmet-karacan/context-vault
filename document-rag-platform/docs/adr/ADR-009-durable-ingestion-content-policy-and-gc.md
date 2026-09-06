# ADR-009: Durable ingestion, content policy, encrypted artifacts, and GC

- Status: Accepted
- Date: 2026-09-02
- Owner: Mehmet KARACAN
- Decision evidence: migration `cv3_00000004`, A6 integration/fault tests, and
  `artifacts/ingestion/2026-09-02-a6/INGESTION_RECEIPT.json`
- Supersedes: the reindex implementation section of ADR-003

## Context

Upload and repository-like sources previously used separate write paths. DB,
object storage and queue side effects had no durable coordination, a failed
reindex could endanger the active version, and API deletion did not establish
an object/chunk/vector retention lifecycle. Original artifacts also required a
deployment-independent encryption boundary.

## Decision

- `IngestionOrchestrator` is the only production coordinator for upload,
  repository, directory, archive, reindex, retry and cancellation.
- Source acceptance writes to a deterministic encrypted staging key, verifies
  checksum, and commits document/version/job/policy/storage registry/outbox in
  one transaction. Outbox and inbox keys are unique; worker claims use leases,
  attempts, heartbeats and terminal receipts.
- MIME is derived from magic, declared MIME and file structure. Mismatch is an
  audit policy event. Credential/private-key content is quarantined before
  storage or remote processing. Confidential business content is not itself a
  credential classification.
- New versions follow build-new → validate → compare-and-swap activate. The old
  version remains readable until activation. Losing concurrent activation is a
  retryable conflict, never a delete-first operation.
- All new MinIO objects use an application-layer AES-256-GCM envelope. Storage
  key is authenticated as AAD. Missing/invalid encryption configuration fails
  startup; legacy plaintext reads are default-deny.
- Document delete is idempotent soft-delete plus object retention scheduling.
  Superseded-version objects receive the same retention window.
- A `message_citation` is a reference/legal hold for its version. Explicit
  `storage_objects.legal_hold` is an additional independent hold. GC requires
  elapsed retention and zero holds/references, supports dry-run, and writes a
  receipt. Only after every referenced object for a version is deleted may its
  artifact, chunk and vector rows be removed.

## Consequences

Queue loss is recoverable from PostgreSQL, worker redelivery converges without
duplicate versions/chunks/artifacts, and a failed candidate cannot erase the
active version. Operations must retain encryption keys for the full artifact
retention period. Citation-bearing versions consume storage until their
citation lifecycle is explicitly resolved.

## Rollback

Disable queue consumers and stop new acceptance, then restore the verified DB
and object-store pair. Production rollback does not use destructive Alembic
downgrade. The `cv3_00000004` downgrade is validated only on isolated disposable
databases and does not authorize deletion of retained artifacts.
