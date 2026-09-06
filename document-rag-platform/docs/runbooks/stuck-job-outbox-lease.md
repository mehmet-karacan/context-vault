# Stuck Job, Outbox and Stale Lease

Last verified SHA/date: `b6d368c4ba75afee5630251e5c0f3649d1c9c920`, `2026-09-06` (transaction-rollback drill on a newly created disposable database; no live repair).

## Prerequisites
Workspace/project scope, incident ID, DB read access and bounded retry authority.

## Dry-run
Read counts/oldest age from `ingestion_jobs`, `outbox_events` and expired leases;
hash identifiers in evidence. On a freshly migrated, labelled disposable database
only, run from `document-rag-platform/services/backend`:

```sh
DATABASE_URL="$CV_DISPOSABLE_DATABASE_URL" .venv/bin/python -m pytest -q \
  tests/integration/test_ingestion_control_plane.py::test_policy_outbox_lease_and_orphan_recovery_paths
```

The test wraps database work in an outer transaction and rolls it back. Never aim
this command at a database that contains user data.

## Exact commands
Use the existing maintenance/retry application operation for one claimed work
item; do not update rows manually. Verify the exact project scope and active lease
owner again immediately before the effect.

## Expected output
One idempotent receipt, monotonic attempt number, cleared lease and decreasing backlog age.

## Stop conditions
Active heartbeat, unknown owner, duplicate terminal receipt, cross-scope row or growing backlog.

## Rollback
Stop dispatch/worker, let lease expire, preserve outbox intent and resume from the last durable receipt.

## Evidence / receipt
Incident ID, hashed work IDs, before/after counts/ages, attempt and terminal receipt hashes.
