# CV3 Baseline Schema and Migration Summary

## Directly verified baseline state

- Migration files: `3`
- Revision chain: `b2f1c0a10001 → b2f1c0a10002 → b2f1c0a10003`
- Source migration head: `b2f1c0a10003`
- Missing exact revisions referenced by historical evidence:
  `b2f1c0a10004`, `b2f1c0a10005`
- Operational V3 migration set: `1` static revision.
- Operational V3 head: `cv3_00000001`.
- The historical three-revision chain remains outside the configured version
  location as incident evidence.
- Alembic imports only `MigrationSettings`; provider, Redis and object-store
  credentials are not required.

## Runtime state

The legacy runtime remains unavailable. An isolated pgvector/PostgreSQL 16 test
runtime was created from a blank database after owner authorization. Its exact
runtime revision is `cv3_00000001`, it has `14` application/Alembic tables and a
single active embedding profile with a non-null deterministic `config_hash`.
The normalized source/restore schema fingerprint is
`55ceada8d3539f817af0a03b11c000abcf6aadb2ee7f248a8343495728814af8`.

The committed 2026-08-31 audit reports runtime revision `b2f1c0a10005`, but that
claim is historical and was not promoted to current evidence.

## Decision

`RECOVER_EXACT_MIGRATIONS` is unsupported by the available evidence. The selected
recovery route is:

`V3_NEW_DB_LINEAGE_RESET_AND_CUTOVER`

Decision state: `AUTHORIZED_AND_ADMITTED`.

The owner accepted the inaccessible legacy data as unrecoverable and authorized
a new empty isolated lineage. Blank upgrade, idempotent re-run,
downgrade/upgrade, startup revision admission and synthetic backup/restore all
passed. No no-op revision, blind stamp, manual version edit or legacy-source
mutation was performed.
