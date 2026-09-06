# Migration, Recovery and Lineage Reset

Last verified date: `2026-09-06`. The A12 candidate was verified from a blank
database through `cv3_00000007`, including a `cv3_00000006` →
`cv3_00000007` sentinel-preservation upgrade and a reversible
`cv3_00000007` → `cv3_00000006` schema downgrade. The last independently
verified restore source SHA remains
`bde49aca525f7ccc40b21c039af003413621223c`; bind the A12 implementation SHA in
the canonical task receipt before release validation.

## Prerequisites
Valid backup+restore receipt, exact DB identity, maintenance window and `MIGRATION_RUNBOOK.md` decision record.

## Dry-run
`DATABASE_URL="$DATABASE_MIGRATION_URL" python scripts/verify_migrations.py --json-output /new/private/path/migration-check.json`

## Exact commands
`docker compose run --rm migration`; rerun the verifier against the restored fresh target before cutover.

## Expected output
One repository head and DB head `cv3_00000007`; invariants zero.

## Stop conditions
Divergent history, dirty/unbacked DB, multiple heads, unknown revision or nonzero invariant.

## Rollback
Restore/cut over to the verified pre-migration snapshot; never force downgrade or lineage stamping.

## Evidence / receipt
Keep before/after schema hashes, backup receipt, migration output and restored-target verification.
