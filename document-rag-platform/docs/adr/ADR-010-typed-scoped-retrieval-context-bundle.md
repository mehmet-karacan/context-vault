# ADR-010: Typed, scoped retrieval and canonical ContextBundle

- Status: Accepted
- Date: 2026-09-02
- Owner: Mehmet KARACAN
- Decision evidence: migration `cv3_00000005`, typed retrieval tests, synthetic
  benchmark and `artifacts/retrieval/2026-09-02-a7/RETRIEVAL_RECEIPT.json`
- Supersedes: the untyped result model and legacy-vector transition in ADR-004

## Context

The hybrid retrievers used a mutable, loosely shaped candidate. Rank rebuilding
could discard reranker scores, same-list duplicates could over-contribute to
RRF, adjacent lookup was under-scoped, and the model-facing context was not a
single provenance object. A second legacy vector column also permitted profile
ambiguity.

## Decision

- Dense, lexical and identifier repositories require the complete immutable
  `RetrievalScope`: principal-derived workspace, project, active version,
  source/classification policy and embedding profile. Missing or unknown scope
  input fails closed before SQL.
- The stage contracts are immutable `RetrieverHit`, `FusedHit`, `RerankedHit`,
  `ContextItem` and `ContextBundle` values. RRF records one contribution per
  retriever and chunk. Final ranking wraps the fused hit, retaining all earlier
  scores and match provenance.
- `chunk_embeddings` is the sole vector authority. Migration `cv3_00000005`
  aborts transactionally if a populated legacy vector has no canonical copy;
  only then does it remove `chunks.embedding`. Its isolated downgrade restores
  that column from the version's canonical profile.
- Lexical default is `websearch_to_tsquery(simple)`; quoted queries use
  `phraseto_tsquery(simple)`. Every chunk records `search_profile`. Identifier
  matching separates exact, normalized, qualified, prefix, trigram and
  explicitly opted-in substring modes; no default leading wildcard exists.
- HNSW tuning is transaction-local. The bounded synthetic benchmark selected
  `ef_search=20` after all tested values achieved recall@10 1.0; production
  decisions must be re-benchmarked on a representative corpus.
- Parent and neighbor resolution carries workspace, project, document, version,
  source file and sequence identity. Cross-boundary rows are rejected.
- `ContextBundle.selected_items` is the only model prompt input. Rejected items
  and reasons stay in debug provenance, and debug projections omit content.
- Each request writes content-free `retrieval_runs` provenance using a normalized
  query hash, fixed-cardinality stage metrics, counts, timings, fallback/no-answer
  reason and bundle hash. Slow stages are logged without raw content; slow SQL
  can emit an index-miss trace after a query-plan check.

## Consequences

Retrieval cannot silently widen across a project/version/profile boundary.
Reranker failure is explicit and returns fusion order without inventing a score.
The stricter repository signature intentionally breaks unscoped callers. The
context bundle may contain rejection metadata, but only selected content reaches
the model.

## Rollback

Stop new retrieval traffic and use the verified code/migration pair. Production
rollback does not run a destructive downgrade. The isolated downgrade path
reconstructs the legacy vector from canonical profile-bound embeddings and is a
test/recovery mechanism only.
