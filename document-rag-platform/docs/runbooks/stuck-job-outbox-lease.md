# Stuck Job, Outbox and Stale Lease

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (query contract review; no live repair).

## Prerequisites
Workspace/project scope, incident ID, DB read access and bounded retry authority.

## Dry-run
Read counts/oldest age from `ingestion_jobs`, `outbox_events` and expired leases; hash identifiers in evidence.

## Exact commands
Use the existing maintenance/retry application operation for one claimed work item; do not update rows manually.

## Expected output
One idempotent receipt, monotonic attempt number, cleared lease and decreasing backlog age.

## Stop conditions
Active heartbeat, unknown owner, duplicate terminal receipt, cross-scope row or growing backlog.

## Rollback
Stop dispatch/worker, let lease expire, preserve outbox intent and resume from the last durable receipt.

## Evidence / receipt
Incident ID, hashed work IDs, before/after counts/ages, attempt and terminal receipt hashes.
