# A11 Backup, Restore and Object Reconciliation

Last verified SHA: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`
Last verified date: `2026-09-06`
Verified scope: disposable pgvector/MinIO source and fresh target, one encrypted
synthetic object, custom-format dump, restore, hash verification and reconciliation.

## Prerequisites

- Run with the locked backend Python and Docker/`pg_dump` 16-compatible tooling.
- Effectful backup and restore require a clean Git worktree. The source backup and
  restore executor revisions are recorded separately; never treat a dirty-tree
  rehearsal as canonical evidence.
- Put DB, MinIO and encryption-key values only in named environment variables.
- Source bucket versioning must already be `Enabled`; the backup command never
  enables it on a live bucket. At least one encrypted object is required so the
  supplied key can be authenticated rather than merely shape-checked.
- Backup directory must not exist. It is private (`0700`); manifest, dump and
  object payloads are `0600`. The private manifest necessarily contains object
  keys and must never enter Git.
- Backup requires the exact source PostgreSQL container. The tool resolves its
  immutable container ID and records its real `system_identifier` and database.
- Restore PostgreSQL must be a different cluster in a running container carrying
  `com.context-vault.restore-target=true`. The target database must not exist; the
  tool creates it inside the inspected immutable container ID.
- Restore MinIO must resolve to a different IP/port from the source and the target
  bucket must not exist; the tool creates it and enables versioning.
- Obtain the private manifest SHA-256 from the separately retained backup receipt.
  Never derive/accept the expected value from the manifest being admitted.
- Keep the application stopped or disconnected from the fresh target.

## Dry-run

```sh
umask 077
python scripts/backup_restore.py --dry-run --retention-days 30 \
  --json-output /new/private/path/backup-restore-plan.json
```

Expected: exit `0`, `status=DRY_RUN`, `effects_performed=false`.

## Exact commands

Backup:

```sh
python scripts/backup_restore.py --backup --retention-days 30 \
  --database-url-env DATABASE_MIGRATION_URL \
  --postgres-container context-vault-postgres-1 \
  --minio-endpoint-env MINIO_ENDPOINT \
  --minio-access-key-env MINIO_ROOT_USER \
  --minio-secret-key-env MINIO_ROOT_PASSWORD \
  --minio-bucket-env MINIO_BUCKET \
  --encryption-key-env OBJECT_STORAGE_ENCRYPTION_KEY \
  --backup-dir /new/private/path/cv-backup-YYYYMMDDTHHMMSSZ \
  --json-output /new/private/path/backup-receipt.json
```

## Expected output

Expected: non-empty custom dump; versioning enabled; every current object has a
`CVENC1` envelope; inventory/payload hashes and retention are bound by the receipt.
This is a current-state restore point. Run it automatically at the approved RPO
interval (example: cron `17 */6 * * *` under a dedicated backup identity); alert if
the last successful receipt is older than the RPO. Never embed secrets in cron.

## Restore-point selection and exact restore command

Choose the newest completed manifest whose `completed_at_utc` is not later than the
incident time. Record the choice and immutable backup-manifest SHA. Then:

```sh
python scripts/backup_restore.py --restore-drill \
  --target-database-url-env CV_RESTORE_DATABASE_URL \
  --target-postgres-container cv-restore-postgres \
  --minio-endpoint-env MINIO_ENDPOINT \
  --minio-bucket-env MINIO_BUCKET \
  --target-minio-endpoint-env CV_RESTORE_MINIO_ENDPOINT \
  --target-minio-access-key-env CV_RESTORE_MINIO_ROOT_USER \
  --target-minio-secret-key-env CV_RESTORE_MINIO_ROOT_PASSWORD \
  --target-minio-bucket-env CV_RESTORE_MINIO_BUCKET \
  --encryption-key-env OBJECT_STORAGE_ENCRYPTION_KEY \
  --expected-manifest-sha256 "$CV_EXPECTED_BACKUP_MANIFEST_SHA256" \
  --backup-dir /private/path/selected-backup \
  --json-output /new/private/path/restore-receipt.json
```

Expected: `status=PASS`, missing/orphan counts `0`; every AES-GCM envelope has been
authenticated with the object key as AAD without retaining plaintext; immutable container and cluster
identity hashes and exact clean executor revision present; migration, auth, retrieval and citation smoke all true;
`rpo_seconds` and `rto_seconds` measured. Object payloads are re-downloaded and
SHA-verified after restore. The `--json-output` path must be new; it is reserved
with `O_EXCL`/`O_NOFOLLOW` before credentials are read or any restore effect starts.

## Stop conditions

Stop on disabled versioning, missing/invalid encryption key, plaintext object,
expired retention, absent/existing output, manifest SHA/schema/size mismatch,
empty/changed dump, changed object bytes, missing/duplicate target container,
missing restore-target label, same PostgreSQL `system_identifier`, existing target
database/bucket, overlapping resolved MinIO IP/port, schema-head mismatch, any smoke
failure, missing/orphan object, or credential/path appearing in a public receipt.

## Rollback

Do not mutate the source. On failure retain the private failed evidence, disconnect
the fresh target, then remove only the exact drill containers/bucket/DB after their
identities are rechecked. Production rollback is a cutover back to the prior intact
deployment or a new restore; never drop/clean the source.

## Evidence / receipt

Retain the private manifest/dump/object directory under the approved 30-day policy
and access-controlled key escrow. Publish only hashes, counts, encryption/versioning
status, smokes and RPO/RTO. A restore receipt never authorizes release by itself.
