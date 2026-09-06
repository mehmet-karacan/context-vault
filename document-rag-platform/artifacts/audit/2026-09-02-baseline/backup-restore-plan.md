# CV3 Backup and Restore Admission Plan

Status: `PASS_FOR_NEW_ISOLATED_V3_LINEAGE`

The legacy source remained unavailable. The repository owner explicitly
authorized an empty isolated V3 lineage and accepted that the inaccessible
legacy data cannot be recovered from the evidence currently available. This
does not authorize deletion if a legacy source is discovered later.

## Admission inputs required

1. Exact source PostgreSQL identity and a read-only connection method.
2. Exact source object-store identity and read-only inventory access.
3. Owner-confirmed evidence classification and encrypted backup destination.
4. Free-space check and retention period.
5. Source Alembic revision, normalized schema fingerprint, table counts, selected
   invariant fingerprints, extension list, and object-reference counts.

No credential value may be written to Git, shell history, a public receipt, or a
provider prompt.

## Backup set

- PostgreSQL schema-only dump.
- PostgreSQL custom-format full dump.
- Sanitized role and extension metadata.
- Deterministically sorted table counts and invariant fingerprints.
- Object inventory containing only bucket class, hashed object key, size,
  checksum/etag, and version marker.
- Secret-free compose/deployment manifest and image digests.
- SHA-256 ledger for every private artifact.

The database snapshot and object inventory must represent a documented
consistency point. A file existing on disk is not accepted as a backup until the
restore drill passes.

## Isolated restore target

- New project name and new volumes, never shared with the source stack.
- PostgreSQL/pgvector image pinned by digest after provenance verification.
- Separate empty object-store bucket with isolated credentials.
- Host ports bound only to loopback or omitted.
- No backend, worker, provider, or remote embedding/generation process started
  until restore verification is complete.
- Destructive cleanup commands may target only the recorded temporary project
  and require a terminal restore receipt first.

## Restore verification

1. Restore schema and data with stop-on-error behavior.
2. Recompute schema fingerprint, counts, foreign-key and domain invariants.
3. Import objects into the isolated bucket and reconcile every DB reference.
4. Verify checksums without publishing object names or content.
5. Run read-only application smoke against the restored copy.
6. Record RPO/RTO, exit codes, image digests, artifact hashes, and rollback.
7. Keep the original source read-only. Cutover requires separate written owner
   approval.

Stop immediately on source identity ambiguity, checksum mismatch, missing object,
schema drift, insufficient storage, or any command that resolves to the source
instead of the isolated target.

## Executed synthetic restore drill — 2026-09-02

- PostgreSQL image: `pgvector/pgvector@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`.
- MinIO image: `minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`.
- Database migration revision: `cv3_00000001`.
- Source and separate restore database aggregate counts: `1/1/1/1` for
  project/document/version/chunk.
- Source and restore normalized schema fingerprint:
  `55ceada8d3539f817af0a03b11c000abcf6aadb2ee7f248a8343495728814af8`.
- Custom-format dump SHA-256:
  `d03b3917dd2b4f207e7e92352b4c0df91406c058e78d54d057564ecbfd553804`.
- Source and separate restore bucket inventory count: `1/1`.
- Restored object byte SHA-256:
  `39bccd0bc73b6af8397a4a4e3274a0543aa1dbf4e0c4eeff2f58884d814dc93b`.
- Backend/worker/provider processes were not started; no remote provider call
  occurred.
