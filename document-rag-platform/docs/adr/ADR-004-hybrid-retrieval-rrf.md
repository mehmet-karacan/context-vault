# ADR-004 — Hybrid Retrieval & Reciprocal Rank Fusion (RRF)

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Context Vault platform implementation (Aşama 5)

## Context

Queries mix natural language (robust against Turkish morphology) and precise
technical identifiers (function names, symbols, exact column refs). A single
scoring method fails on one axis: dense cosine and lexical `ts_rank` live on
incompatible scales, so raw scores cannot be summed directly
(`AKTIF_GOREV.md §5`).

## Decision

- Run three retrievers in parallel (`application/retrieval_service.py`,
  orchestrated end-to-end by `RetrievalService`):
  - `infrastructure/retrieval/dense.py` — pgvector (`DenseVectorRetriever`);
  - `infrastructure/retrieval/lexical.py` — Postgres full-text over
    `chunks.search_vector` using the **`simple`** text-search config
    (`DEFAULT_TEXT_SEARCH_CONFIG = "simple"` in `config.py`, so
    `PAYMENT_FLAG`-style identifiers survive unstemmed);
  - `infrastructure/retrieval/identifier.py` — exact symbol/identifier match.
- Fuse with Reciprocal Rank Fusion, deterministic and score-scale-agnostic:
  `infrastructure/retrieval/rrf.py` (`reciprocal_rank_fusion`, `fuse`,
  `dedupe`); score `1/(k + rank)` with `RRF_K = 60` (`config.py`).
- Then: dedupe identical-content copies → optional feature-gated rerank
  (`infrastructure/rerankers/remote.py` / `noop.py`, `build_reranker`) → context
  building with parent/adjacent expansion (`infrastructure/retrieval/context_builder.py`);
  `ContextBuilder` enforces `CONTEXT_MAX_CHUNKS = 8` / `CONTEXT_MAX_TOKENS = 12000`.
- No-answer / intent policy (`infrastructure/retrieval/no_answer.py`):
  `is_smalltalk` + `AnswerPolicy` decide `smalltalk` vs `document` and
  `answerable`; empty retrieval is never auto-small-talk, and an exact
  identifier or strong lexical match can override a low dense score
  (thresholds from settings: `NO_ANSWER_SCORE_THRESHOLD`,
  `NO_ANSWER_MIN_EVIDENCE`, `LEXICAL_STRONG_SCORE`).
- Candidate counts / fusion window (`config.py`): `VECTOR_CANDIDATE_K = 40`,
  `LEXICAL_CANDIDATE_K = 40`, `IDENTIFIER_CANDIDATE_K = 20`,
  `FUSION_CANDIDATE_K = 20`, `RRF_K = 60`, `RERANK_TOP_K = 8`.

## Consequences

- Technical identifiers are retained (Turkish-robust lexical "simple" config +
  identifier retriever) while dense embeddings capture semantic meaning.
- Fusing by rank rather than raw score keeps dense/lexical scales comparable.
- Deterministic, DB-free fusion core is unit-testable; budgets are configurable.
