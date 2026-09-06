# Context Vault V3 Migration Runbook

Status: verified through the durable-ingestion migration on 2026-09-02.

Operational head: `cv3_00000004`
Configured versions directory: `alembic/versions_v3/`

The files under `alembic/versions/` are legacy incident evidence. They are not
an operational migration source and must not be copied, stamped or combined
with the V3 graph.

## Safety rules

- Never run `alembic stamp`, edit `alembic_version`, or guess missing legacy
  revisions.
- Never migrate a database whose identity and backup provenance are unknown.
- Never run downgrade against a database containing retained user data.
- Application startup is read-only: it checks the exact revision and performs
  no DDL or repair.
- Production rollback uses verified restore/cutover. Downgrade is limited to a
  disposable empty test database.
- LLM, embedding, Redis and MinIO credentials are not migration inputs.

## Configuration

The deployment supplies two distinct URLs:

```text
DATABASE_MIGRATION_URL  migration owner; one-shot only
DATABASE_RUNTIME_URL    backend, worker and scheduler only
```

Compose maps `DATABASE_MIGRATION_URL` to `DATABASE_URL` only inside the
migration one-shot. Alembic reads only these variables through
`MigrationSettings`:

```text
DATABASE_URL                   required
DATABASE_SCHEMA                default: public
MIGRATION_LOCK_TIMEOUT_MS      default: 5000
MIGRATION_STATEMENT_TIMEOUT_MS default: 300000
```

Do not print or persist either URL. Public receipts contain only an opaque
environment reference, non-secret role names and hashes. Runtime services must
never receive the migration-owner URL.

## Admission before any migration

1. Resolve the exact database identity and confirm it is the intended target.
2. Confirm the target is new/empty, or stop and use the restore/import process.
3. Pin the PostgreSQL/pgvector image by digest.
4. Capture a custom-format dump and object inventory when a source exists.
5. Restore both into separate isolated targets and verify checksums.
6. Run the migration verifier without `--upgrade` first.

The 2026-09-02 legacy source was unavailable. The owner authorized a new empty
lineage; this is not permission to overwrite a legacy source found later.

## Blank database upgrade

For a brand-new empty PostgreSQL volume, the image evaluates
`infra/docker/postgres/10-runtime-role.sql`. It creates a distinct login role
with `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`,
`NOREPLICATION` and `NOBYPASSRLS`, and revokes schema creation. This initializer
does not rerun for an existing cluster. Never erase a volume to force it to run;
provision the role through an approved database-administration procedure
instead.

From `document-rag-platform/services/backend/`, with `DATABASE_URL` supplied by
the deployment secret mechanism:

```sh
python -m alembic heads
python -m alembic upgrade head
python -m alembic current
python -m alembic upgrade head
```

Expected head/current: `cv3_00000004`. The second upgrade must be a no-op.

Immediately after `upgrade head`, the Compose migration one-shot runs
`infra/docker/postgres/apply_runtime_grants.py` with the migration-owner URL.
It applies current table/sequence/function grants, installs owner-scoped
default privileges for future migration objects, and fails unless the runtime
role remains unable to create temporary objects, create in the application
schema, or own the database/schema. Require exactly `runtime database grants:
PASS` before starting runtime services.

The isolated regression drill uses a unique disposable container and volume:

```sh
A11_RUN_DISPOSABLE_DB_TEST=1 .venv/bin/pytest -q \
  tests/test_deployment_contract.py -m integration
```

It must prove DML and function access on both existing and later-created
objects, and SQLSTATE `42501` for table/schema/role DDL. It is not authorization
to run against a retained database.

Then run from the repository root:

```sh
python scripts/verify_migrations.py --strict --database-url "$DATABASE_URL"
```

The JSON result must be `PASS`, the graph must have one head, runtime revision
must equal the expected head and every invariant count must be zero.

## Disposable downgrade cycle

Only on an explicitly identified empty/disposable database:

```sh
python -m alembic downgrade base
python -m alembic upgrade head
python scripts/verify_migrations.py --strict --database-url "$DATABASE_URL"
```

Do not treat this as production rollback evidence.

## Backup and restore drill

PostgreSQL backup must be custom format and stop on restore errors:

```sh
pg_dump --format=custom --file=context-vault.dump "$DATABASE_URL"
createdb context_vault_restore
pg_restore --exit-on-error --dbname=context_vault_restore context-vault.dump
```

The actual secret-bearing invocation belongs in private operational tooling.
Record only:

- dump SHA-256;
- image digest and database major/minor version;
- source/restore schema fingerprints;
- deterministic table counts and invariant counts;
- start/finish time and RPO/RTO;
- opaque private-artifact reference.

For MinIO/S3, capture a deterministically sorted inventory containing hashed
keys, version identifier, size and checksum. Restore into a separate bucket and
compare inventory and downloaded byte hashes. A copied file is not a verified
backup until restore succeeds.

## Startup and readiness

After migration, application startup calls `init_db()`, which runs only:

```sql
SELECT version_num FROM alembic_version;
```

Missing `alembic_version`, an unreachable DB, or any revision other than the
compiled expected head causes startup/readiness failure. Startup never creates
extensions, tables or indexes.

## Failure handling

- Lock/statement timeout: stop; identify the lock owner and retry only after a
  safe operational decision.
- Multiple heads: fail CI and reconcile the source graph; do not merge heads at
  runtime.
- Revision ahead/behind: stop startup and deploy the matching code/migration.
- Partial data operation: resume the separate receipt-producing data command;
  do not hide it with an Alembic stamp.
- Restore mismatch: quarantine the candidate target and retain source/backup.
- Legacy source discovered: keep it read-only and open a new provenance,
  fingerprint and import admission.

## Verified 2026-09-02 evidence

- Disposable `cv3_00000003 → cv3_00000004 → cv3_00000003 → cv3_00000004`:
  PASS; `alembic check` reported no new upgrade operations.
- Strict verifier at `cv3_00000004`: PASS; 255 columns, 110 constraints, one
  active profile and 17 measured invariant counts all zero.
- Provider credentials loaded by migration verifier: `false`.
- `cv3_00000004` schema SHA-256:
  `7328301b5e7ccd22ba66b1c4e3c23292ac751049ede639628cae1dca339d087b`.

- Blank `base → cv3_00000003`: PASS.
- Re-run `upgrade head`: PASS/no-op.
- Disposable `cv3_00000003 → cv3_00000002 → cv3_00000003`: PASS.
- Recovered fixture `cv3_00000001 → cv3_00000003`: PASS with project,
  document, version, chunk, profile counts preserved at `1/1/1/1/1`.
- Migration without provider/object-store credentials: PASS.
- Exact startup revision admission: PASS.
- PostgreSQL custom dump SHA-256:
  `d03b3917dd2b4f207e7e92352b4c0df91406c058e78d54d057564ecbfd553804`.
- Source/restore normalized schema SHA-256:
  `55ceada8d3539f817af0a03b11c000abcf6aadb2ee7f248a8343495728814af8`.
- Source/restore aggregate row counts: equal.
- Source/restore MinIO inventory and byte checksum: equal.

Public receipt:
`document-rag-platform/artifacts/audit/2026-09-02-baseline/PREPARE_RECEIPT-002.json`.
