# ADR-007: Reset the pre-1.0 migration lineage for Context Vault V3

- Status: Accepted
- Date: 2026-09-02
- Owner: Mehmet KARACAN
- Decision evidence: `artifacts/audit/2026-09-02-baseline/OWNER_DECISION_RECEIPT.json`
- Supersedes: legacy operational use of revisions `b2f1c0a10001` through
  `b2f1c0a10005`

## Context

The repository contains exact revisions `b2f1c0a10001`–`b2f1c0a10003` while
historical runtime evidence refers to unavailable revisions `b2f1c0a10004` and
`b2f1c0a10005`. Exhaustive read-only recovery inventory found no exact files,
Git objects, workflow artifact, backup, database volume or object inventory.
Creating guessed/no-op revisions or stamping a database would manufacture
history and could corrupt user data.

## Decision

Adopt `V3_NEW_DB_LINEAGE_RESET_AND_CUTOVER` with the static, single-head
`cv3_00000001` baseline in `alembic/versions_v3/`. The prior files remain as
incident evidence outside Alembic's configured version location.

The baseline is valid only for a new empty database. It must never be stamped
onto a legacy database. Application startup performs an exact read-only head
check. Extension/table/index creation belongs exclusively to migrations.

The repository owner explicitly accepted the inaccessible legacy data as
unrecoverable and authorized creation of an empty isolated V3 lineage. This
does not authorize deletion or mutation of a legacy source discovered later.

## Consequences

- Alembic no longer requires LLM, embedding, Redis or MinIO credentials.
- Blank installs have one deterministic head and a seeded embedding profile
  with a deterministic configuration hash.
- Existing-data import remains a separately admitted, idempotent operation if
  a legacy source is ever recovered.
- Production rollback is restore/cutover, not blind downgrade.
- Pre-1.0 deployments cannot silently upgrade into V3 by stamping.

## Verification

An isolated pgvector/PostgreSQL runtime passed blank upgrade, no-op re-run,
downgrade/upgrade and synthetic backup-to-separate-database restore. An
isolated MinIO runtime passed source-to-separate-bucket inventory and byte-hash
comparison. Exact public-safe hashes are recorded in the admission receipt.

## Rollback

Before production adoption, revert the V3 lineage commits. After data exists,
restore the verified pre-cutover backup into a separate environment and switch
traffic only after smoke/invariant verification. Never rewrite the live
`alembic_version` row.
