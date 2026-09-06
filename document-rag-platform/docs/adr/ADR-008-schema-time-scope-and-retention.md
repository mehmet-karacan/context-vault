# ADR-008: Schema scope, UTC time, profile activation, and soft deletion

- Status: Accepted
- Date: 2026-09-02
- Owner: Mehmet KARACAN
- Decision evidence: migration `cv3_00000003`, integration invariant tests, and
  `artifacts/performance/2026-09-02-schema-index-evidence.md`
- Supersedes: none

## Context

The V3 schema needs database-enforced identity, version, embedding, state, and
time invariants. Existing timestamp columns were PostgreSQL `timestamp without
time zone`, created by application calls to `datetime.utcnow()`. Projects,
documents, and conversations also need a deletion policy that does not erase
retained user data during ordinary API operations.

## Decision

- Workspace membership grants access to every non-deleted project in that
  workspace. There is no separate project-membership table in this policy.
- Audit events always identify an authenticated principal and workspace; a
  project is linked when the event is project-scoped.
- The existing naive timestamps are interpreted as UTC during the one-way
  production migration to timezone-aware PostgreSQL timestamps. Application
  time comes from the `Clock` abstraction and is always UTC-aware.
- The active embedding profile is deployment-global. The indexed vector
  columns have a fixed dimension of 1024, and a partial unique index permits
  exactly one active profile. Profile identity fields are immutable.
- Project, document, and conversation API deletion is soft deletion. Default
  reads and retrieval exclude deleted rows. Physical garbage collection is a
  later retention-controlled operation and must not be inferred from an API
  delete.
- `chunk_embeddings` is the canonical dense-vector source. The legacy
  `chunks.embedding` column is copied without deletion and no longer read at
  runtime. It may be dropped only after a separate usage audit, backup/restore
  proof, and rollback plan.

## Consequences

Cross-document version references, invalid active versions, terminal job
restarts, mutable embedding identities, invalid statuses, and missing error
details are rejected by PostgreSQL even if application code is wrong. A
workspace role is intentionally broad; finer-grained project membership would
require a new policy decision and migration.

## Rollback

Production rollback is restore/cutover from a verified backup. The Alembic
downgrade exists only for disposable databases; it retains legacy vector data
and converts timestamps back using UTC semantics.
