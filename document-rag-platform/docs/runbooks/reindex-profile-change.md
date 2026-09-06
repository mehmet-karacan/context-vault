# Reindex and Embedding Profile Change

Last verified SHA/date: `b6d368c4ba75afee5630251e5c0f3649d1c9c920`, `2026-09-06` (executable in-memory reindex/profile drills; no provider call or activation effect).

## Prerequisites
Approved model snapshot/profile hash, capacity budget, backup receipt and no active migration.

## Dry-run
From `document-rag-platform/services/backend`, run:

```sh
.venv/bin/python -m pytest -q \
  tests/test_ingestion_tasks.py::test_full_run_completes_and_activates_new_version \
  tests/test_ingestion_tasks.py::test_retry_after_partial_failure_clears_stale_chunks_before_reindexing
```

These tests use only in-memory fakes and make no model/provider or database call.
From the repository root, also run `python scripts/benchmark_retrieval.py --help`
and record current active profile/counts read-only before any real effect.

For the PostgreSQL invariant, use only a newly migrated, labelled disposable
database and run:

```sh
cd document-rag-platform/services/backend
DATABASE_URL="$CV_DISPOSABLE_DATABASE_URL" .venv/bin/python -m pytest -q \
  tests/integration/test_ingestion_control_plane.py::test_reindex_uses_same_orchestrator_and_preserves_active_version_on_failure \
  tests/integration/test_schema_invariants.py::test_database_rejects_cross_version_state_and_profile_violations
```

Both tests roll their transaction back. Never point this command at user data.

## Exact commands
Create a new immutable profile, enqueue bounded reindex jobs, verify shadow
retrieval, then atomically activate it through the scoped application API. Do not
update `embedding_profiles.is_active` manually.

## Expected output
No mixed dimensions/profileless embeddings; recall/citation gates meet the approved baseline.

## Stop conditions
Dimension drift, budget breach, profile leakage, stale job or quality regression.

## Rollback
Reactivate the prior profile; retain new rows for diagnosis, do not bulk-delete embeddings.

## Evidence / receipt
Profile/model/config hashes, counts, benchmark comparison and activation audit event.
