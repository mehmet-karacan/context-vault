# Reindex and Embedding Profile Change

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (procedure cross-checked with `reindex.md`).

## Prerequisites
Approved model snapshot/profile hash, capacity budget, backup receipt and no active migration.

## Dry-run
`python scripts/benchmark_retrieval.py --help` and record current active profile/counts without writes.

## Exact commands
Create a new immutable profile, enqueue bounded reindex jobs, verify shadow retrieval, then atomically activate it through the application API.

## Expected output
No mixed dimensions/profileless embeddings; recall/citation gates meet the approved baseline.

## Stop conditions
Dimension drift, budget breach, profile leakage, stale job or quality regression.

## Rollback
Reactivate the prior profile; retain new rows for diagnosis, do not bulk-delete embeddings.

## Evidence / receipt
Profile/model/config hashes, counts, benchmark comparison and activation audit event.
