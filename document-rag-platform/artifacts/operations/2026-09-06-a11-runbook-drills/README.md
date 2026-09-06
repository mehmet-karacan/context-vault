# A11 Automatic Backup and Runbook Drill Evidence

This package records bounded local evidence for the A11 automatic-backup
scheduler contract and the operational runbooks required by §18.7. The tested
base repository revision was
`b6d368c4ba75afee5630251e5c0f3649d1c9c920`; the uncommitted scheduler/runbook
source bytes are bound separately in `SOURCE_MANIFEST.json`.

The scheduler was exercised in `DRY_RUN` mode only. It resolved the exact base
revision but did not read credentials, invoke `backup_restore.py`, create the
configured backup root, connect to PostgreSQL/MinIO, or create a backup. Effectful
automatic scheduling and production alert delivery remain unclaimed.

The scheduler unit contract verifies:

- exact SHA and clean-tree admission before an effect;
- an owner-only external backup root and non-symlink private files;
- a global non-blocking lock for overlap prevention;
- UTC interval-slot plus source-revision idempotency;
- fail-closed handling for partial, tampered or failed child runs;
- credential values only in a minimal child environment, never argv/receipts;
- exact child receipt and private manifest SHA-256 binding;
- a retention-not-before timestamp without automatic deletion;
- exclusive mode-`0600` terminal receipts and no raw private paths.

The runbook drills used mocks/in-memory fakes except for four selected integration
tests. Those four ran against a newly created, explicitly labelled disposable
pgvector PostgreSQL database migrated to `cv3_00000006`. Every test transaction
rolled back. No project, ingestion-job or GC-receipt row remained; the freshly
migrated schema's single inactive legacy-owner principal remained. The exact
disposable container plus anonymous volume were removed. No MinIO object, user database,
production secret, provider, network target, release, traffic route or remote
ruleset was mutated.

All JSON and checksums in this directory are public-safe. Container/volume
identities are hash-only. This evidence does not activate a production scheduler,
approve a release, perform credential revocation or prove alert delivery.
