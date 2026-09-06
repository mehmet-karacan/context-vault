# Scoped retrieval operations

- Owner: Mehmet KARACAN
- Last verified: 2026-09-02
- Evidence: `artifacts/retrieval/2026-09-02-a7/`

## Preconditions

1. Confirm the deployed code SHA and the database Alembic revision are the
   receipt's pair.
2. Confirm exactly one active 1024-dimensional embedding profile exists.
3. Run the strict migration verifier. Do not proceed if any invariant is
   non-zero or `chunks.embedding` still exists at `cv3_00000005`.
4. Never repair retrieval by resetting a database or deleting object storage.

## Diagnosis

- Inspect the content-free `retrieval_runs` row for query hash, stage latency,
  candidate/selected counts, no-answer and fallback reason.
- Use the admin-only debug endpoint only where enabled. Its projection contains
  IDs, hashes, scores and rejection reasons, not full context text.
- For a slow stage, inspect the structured `slow_query` event. When plan tracing
  is enabled, an absent expected index emits `index_miss`; reproduce with
  `EXPLAIN` before rebuilding an index.
- Validate HNSW, FTS and trigram index eligibility with the integration plan
  test. Forced `enable_seqscan=off` in that test proves eligibility, not that a
  production planner must always choose the index for tiny tables.

## Safe index response

Use `REINDEX INDEX CONCURRENTLY` or a concurrently built replacement according
to PostgreSQL operational policy. Record lock/statement timeouts and verify the
plan after the change. Do not change vector dimension or active profile in
place; follow the embedding-model-change runbook and rebuild a new version.

## Benchmark

Run `scripts/benchmark_retrieval.py` only against an authorized non-production
PostgreSQL database. It creates temporary synthetic tables, rolls back all
rows, loads no provider credentials, and emits aggregate JSON. Corpus-level
production tuning requires a separately approved, redacted evaluation set.
