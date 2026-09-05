# Migration, Recovery and Lineage Reset

Last verified SHA/date: `7d36fc74143d7504113ba6ac3adeaf9d736537bc`, `2026-09-06` (fresh `cv3_00000006` Compose migration and doctor head query); restore source SHA `bde49aca525f7ccc40b21c039af003413621223c`.

## Prerequisites
Valid backup+restore receipt, exact DB identity, maintenance window and `MIGRATION_RUNBOOK.md` decision record.

## Dry-run
`DATABASE_URL="$DATABASE_MIGRATION_URL" python scripts/verify_migrations.py --json-output /new/private/path/migration-check.json`

## Exact commands
`docker compose run --rm migration`; rerun the verifier against the restored fresh target before cutover.

## Expected output
One repository head and DB head `cv3_00000006`; invariants zero.

## Stop conditions
Divergent history, dirty/unbacked DB, multiple heads, unknown revision or nonzero invariant.

## Rollback
Restore/cut over to the verified pre-migration snapshot; never force downgrade or lineage stamping.

## Evidence / receipt
Keep before/after schema hashes, backup receipt, migration output and restored-target verification.
