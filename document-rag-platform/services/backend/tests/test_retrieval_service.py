"""Aşama 5 coordinated retrieval service tests (application layer).

These tests exercise :class:`RetrievalService` end-to-end but 100% DB-free: the
three retrievers are injected fakes returning canned ``RetrievalCandidate``
lists, chunks are resolved from an in-memory dict, and a small fake reranker /
tight token budget verify the rerank and context paths. Covers:

- fusion order is RRF-correct,
- dedupe removes identical-content copies,
- reranker is called when enabled else behaves as a noop (fusion order),
- context is built within the budget,
- answerable / intent classification is present and correct,
- the debug payload contains per-stage ranks / scores / labels.
"""

import pytest
from dataclasses import replace
from uuid import UUID

from src.application.retrieval_service import (
    QueryEmbeddingError,
    RetrievalResult,
    RetrievalService,
    dict_chunk_resolver,
)
from src.domain.retrieval_scope import RetrievalScope
from src.infrastructure.retrieval.base import RetrievalCandidate
from src.infrastructure.retrieval.context_builder import ContextBuilder
from src.infrastructure.retrieval.no_answer import INTENT_DOCUMENT, INTENT_SMALLTALK

SCOPE = RetrievalScope(
    principal_id="11111111-1111-4111-8111-111111111111",
    workspace_id="22222222-2222-4222-8222-222222222222",
    project_id="33333333-3333-4333-8333-333333333333",
    embedding_profile_id="66666666-6666-4666-8666-666666666666",
)


class FakeRetriever:
    """In-memory retriever returning a canned candidate list per call."""

    def __init__(self, candidates: list, candidate_k: int = 40):
        self._candidates = list(candidates)
        self.candidate_k = candidate_k
        self.calls = []

    def resolve_k(self, top_k=None):
        return top_k if top_k and top_k > 0 else self.candidate_k

    def search(self, query, top_k, filters=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        return list(self._candidates[:top_k])


class RecordingReranker:
    """Noop reranker that records whether it was invoked."""

    provider = "test"
    model = "test-noop"

    def __init__(self):
        self.calls = 0
        self.last_top_k = None

    def rerank(self, query, candidates, top_k):
        self.calls += 1
        self.last_top_k = top_k
        return list(candidates)


class ReversingReranker(RecordingReranker):
    """Re-orders the fused candidates to prove the reranker was honored."""

    def rerank(self, query, candidates, top_k):
        super().rerank(query, candidates, top_k)
        return list(reversed(candidates))[:top_k]


class ScoringReranker(RecordingReranker):
    provider = "test"
    model = "scorer-v1"

    def __init__(self):
        super().__init__()
        self.contents = []

    def rerank(self, query, candidates, top_k):
        super().rerank(query, candidates, top_k)
        self.contents = [candidate.chunk["content"] for candidate in candidates]
        return [
            replace(candidate, rerank_score=0.77) for candidate in candidates[:top_k]
        ]


def cand(chunk_id, rank, score, source, meta=None):
    return RetrievalCandidate(
        chunk_id=chunk_id,
        rank=rank,
        score=score,
        source=source,
        metadata=dict(meta or {}),
    )


def chunk(chunk_id, content, **kw):
    d = {
        "chunk_id": chunk_id,
        "source_id": "src-1",
        "chunk_type": "document",
        "content": content,
        "sequence_no": kw.get("sequence_no", 1),
        "heading_path": kw.get("heading_path", []),
        "locator": kw.get("locator", {}),
        "content_hash": kw.get("content_hash", f"hash-{chunk_id}"),
        "workspace_id": str(SCOPE.workspace_id),
        "project_id": str(SCOPE.project_id),
        "document_id": kw.get("document_id", "77777777-7777-4777-8777-777777777777"),
        "version_id": kw.get("version_id", "88888888-8888-4888-8888-888888888888"),
        "source_file_id": kw.get("source_file_id"),
        "metadata": kw.get("metadata", {}),
    }
    return d


def build_service(dense, lexical, identifier, **kw):
    pool = {c["chunk_id"]: c for c in kw.pop("_pool", [])}
    return RetrievalService(
        dense_retriever=FakeRetriever(dense),
        lexical_retriever=FakeRetriever(lexical),
        identifier_retriever=FakeRetriever(identifier),
        chunk_resolver=dict_chunk_resolver(pool),
        embedder=lambda query: [0.0] * 1024,
        **kw,
    )


def test_rrf_fusion_order_is_correct():
    dense = [
        cand("A", 1, 0.9, "dense"),
        cand("B", 2, 0.8, "dense"),
        cand("C", 3, 0.7, "dense"),
    ]
    lexical = [cand("D", 1, 0.6, "lexical"), cand("A", 2, 0.5, "lexical")]
    identifier = [cand("E", 1, 1.0, "identifier")]
    pool = [
        chunk("A", "a"),
        chunk("B", "b"),
        chunk("C", "c"),
        chunk("D", "d"),
        chunk("E", "e"),
    ]

    service = build_service(dense, lexical, identifier, _pool=pool)
    result = service.retrieve("some query", SCOPE, debug=True)

    order = [c.chunk_id for c in result.ranked_candidates]
    # RRF(k=60): A = 1/61+1/62 highest; then D(=1/61) ties E(=1/61) -> D by id;
    # E; then B(1/62); C(1/63).
    assert order == ["A", "D", "E", "B", "C"]


def test_dedupe_removes_identical_content_copies():
    # Distinct chunk ids, identical content_hash across two different candidates
    # that both make it past fusion.
    dense = [
        cand("X1", 1, 0.9, "dense", meta={"content_hash": "H"}),
        cand("X2", 2, 0.8, "dense", meta={"content_hash": "H"}),
    ]
    lexical = []
    identifier = []
    pool = [
        chunk("X1", "identical body", content_hash="H"),
        chunk("X2", "identical body", content_hash="H"),
    ]

    service = build_service(dense, lexical, identifier, _pool=pool)
    result = service.retrieve("dup", SCOPE, debug=True)

    ids = [c.chunk_id for c in result.ranked_candidates]
    assert "X1" in ids
    assert "X2" not in ids, "identical-content copy should have been deduped"


def test_reranker_noop_keeps_fusion_order_when_disabled():
    dense = [cand("A", 1, 0.9, "dense"), cand("B", 2, 0.8, "dense")]
    lexical = [cand("C", 1, 0.6, "lexical")]
    identifier = []
    pool = [chunk("A", "a"), chunk("B", "b"), chunk("C", "c")]
    reranker = RecordingReranker()

    service = build_service(dense, lexical, identifier, _pool=pool, reranker=reranker)
    result = service.retrieve("q", SCOPE)

    # Noop behaviour: rerank invoked, but fused order preserved.
    # RRF(k=60): A(1/61) ties C(1/61) -> A first by id, then C, then B(1/62).
    assert reranker.calls == 1
    assert reranker.last_top_k == 8
    assert [c.chunk_id for c in result.ranked_candidates] == ["A", "C", "B"]


def test_reranker_reorder_is_honored_when_enabled():
    dense = [cand("A", 1, 0.9, "dense"), cand("B", 2, 0.8, "dense")]
    lexical = [cand("C", 1, 0.6, "lexical")]
    identifier = []
    pool = [chunk("A", "a"), chunk("B", "b"), chunk("C", "c")]
    reranker = ReversingReranker()

    service = build_service(dense, lexical, identifier, _pool=pool, reranker=reranker)
    result = service.retrieve("q", SCOPE)

    assert reranker.calls == 1
    # Reversing reranker flips fused order ["A","C","B"] -> ["B","C","A"].
    assert [c.chunk_id for c in result.ranked_candidates] == ["B", "C", "A"]


def test_final_rank_preserves_fusion_and_reranker_scores():
    reranker = ScoringReranker()
    service = build_service(
        [cand("A", 1, 0.9, "dense")],
        [cand("A", 1, 0.8, "lexical")],
        [],
        _pool=[chunk("A", "evidence")],
        reranker=reranker,
    )
    result = service.retrieve("query", SCOPE, debug=True)
    hit = result.ranked_candidates[0]
    assert hit.reranker_score == 0.77
    assert hit.fused_hit.rrf_score > 0
    assert set(hit.fused_hit.per_retriever_contributions) == {"dense", "lexical"}
    assert reranker.contents == ["evidence"]
    assert result.debug_payload()["context"]["selected_items"][0].get("content") is None


def test_fusion_candidate_budget_is_recorded_in_bundle():
    dense = [cand(f"C{i}", i + 1, 1.0 - i / 100, "dense") for i in range(21)]
    pool = [chunk(f"C{i}", f"evidence {i}") for i in range(21)]
    result = build_service(dense, [], [], _pool=pool).retrieve("query", SCOPE)
    assert result.context is not None
    assert any(
        item.reason == "fusion_candidate_budget"
        for item in result.context.rejected_items
    )


def test_context_built_within_budget():
    # Tight token budget (each chunk = 100 tokens via exact counter).
    dense = [
        cand("A", 1, 0.9, "dense"),
        cand("B", 2, 0.8, "dense"),
        cand("C", 3, 0.7, "dense"),
        cand("D", 4, 0.6, "dense"),
    ]
    lexical, identifier = [], []
    pool = [chunk("A", "aaa"), chunk("B", "bbb"), chunk("C", "ccc"), chunk("D", "ddd")]

    class Exact:
        def count(self, text):
            return 100

    builder = ContextBuilder(token_counter=Exact(), max_tokens=250, max_chunks=8)
    service = build_service(
        dense, lexical, identifier, _pool=pool, context_builder=builder
    )
    result = service.retrieve("q", SCOPE)

    assert result.context is not None
    assert result.context.total_tokens <= 250
    assert len(result.context.items) <= result.context.max_chunks
    assert result.context.truncated is True  # >=3 chunks (300 tokens) exceed 250


def test_answerable_document_question_classified():
    dense = [cand("A", 1, 0.9, "dense")]
    lexical, identifier = [], []
    pool = [chunk("A", "a document body")]

    service = build_service(dense, lexical, identifier, _pool=pool)
    result = service.retrieve("PAYMENT_FLAG nedir?", SCOPE)

    assert result.answerability is not None
    assert result.answerability.intent == INTENT_DOCUMENT
    assert result.answerability.answerable is True


def test_smalltalk_classified_as_intent_smalltalk():
    service = build_service([], [], [])
    result = service.retrieve("selam", SCOPE)

    assert result.answerability.intent == INTENT_SMALLTALK
    assert result.answerability.answerable is False


def test_debug_payload_contains_all_stages_with_ranks():
    dense = [cand("A", 1, 0.9, "dense"), cand("B", 2, 0.8, "dense")]
    lexical = [cand("C", 1, 0.6, "lexical")]
    identifier = [cand("B", 1, 1.0, "identifier")]
    pool = [chunk("A", "a"), chunk("B", "b"), chunk("C", "c")]

    service = build_service(dense, lexical, identifier, _pool=pool)
    result: RetrievalResult = service.retrieve("q", SCOPE, debug=True)

    payload = result.debug_payload()
    stages = payload["stages"]
    assert set(stages) == {"dense", "lexical", "identifier", "fusion", "rerank"}

    # Every serialized stage entry carries chunk_id / rank / score / source.
    for label, items in stages.items():
        assert items, label
        for item in items:
            assert {"chunk_id", "rank", "score", "source"} <= set(item), label

    # The dense stage lists the dense candidate with its source label.
    dense_ser = {c["chunk_id"]: c for c in stages["dense"]}
    assert dense_ser["A"]["source"] == "dense"
    assert dense_ser["A"]["rank"] == 1
    assert dense_ser["A"]["score"] == 0.9

    # Debug stages show fusion + rerank windows too. Fused top: B
    # (dense 1/62 + identifier 1/61) outranks A and C (each 1/61).
    assert stages["fusion"][0]["chunk_id"] == "B"
    assert stages["rerank"][0]["chunk_id"] == "B"


def test_result_to_dict_shape():
    service = build_service([], [], [])
    result = service.retrieve("selam", SCOPE, debug=True)
    d = result.to_dict(debug=True)
    assert d["intent"] == INTENT_SMALLTALK
    assert d["answerable"] is False
    assert "stages" in d["retrieval_debug"]
    assert d["ranked"] == []


class _RecordingSession:
    """Minimal SQLAlchemy-session lookalike recording the executed statement."""

    def __init__(self):
        self.rows = []
        self.executed_sql = None
        self.executed_params = None

    def execute(self, stmt, params=None):
        self.executed_sql = str(stmt)
        self.executed_params = dict(params or {})
        return self

    def fetchall(self):
        return self.rows


def _real_retriever_service():
    """RetrievalService wired to the REAL dense/lexical/identifier retrievers.

    Each retriever holds a recording fake session, so the real
    ``build_spec`` / ``search`` path runs end-to-end without a database. This
    exercises the filter hand-off exactly as the deployment does.
    """
    from src.infrastructure.retrieval.dense import DenseVectorRetriever
    from src.infrastructure.retrieval.identifier import IdentifierRetriever
    from src.infrastructure.retrieval.lexical import LexicalRetriever

    sessions = [_RecordingSession() for _ in range(3)]
    service = RetrievalService(
        dense_retriever=DenseVectorRetriever(session=sessions[0]),
        lexical_retriever=LexicalRetriever(session=sessions[1]),
        identifier_retriever=IdentifierRetriever(session=sessions[2]),
        chunk_resolver=None,
        embedder=lambda query: [0.0] * 1024,
    )
    return service, sessions


def test_real_retriever_path_applies_non_empty_filters():
    # Regression: the service once pre-normalized filters to a List[FilterTerm]
    # and passed that list into each real retriever's search(). Each retriever
    # re-normalizes internally (filter_spec -> normalize_filters -> filters
    # .items()), and a list has no .items(), so ANY non-empty filter set raised
    # AttributeError. Passing the raw dict lets each retriever drive its own
    # normalization and apply the filters.
    service, (dense_s, lexical_s, identifier_s) = _real_retriever_service()

    # Mandatory project scope ---
    result = service.retrieve("PAYMENT_FLAG nasıl set ediliyor?", SCOPE)
    assert result.scope == SCOPE
    for session in (dense_s, lexical_s, identifier_s):
        assert session.executed_sql is not None
        assert "d.project_id =" in session.executed_sql
        assert SCOPE.project_id in session.executed_params.values()

    # Non-empty document and source type restrictions ---
    code_scope = SCOPE.model_copy(
        update={
            "allowed_document_ids": (
                UUID("44444444-4444-4444-8444-444444444444"),
                UUID("55555555-5555-4555-8555-555555555555"),
            ),
            "allowed_source_types": ("repository", "directory", "archive"),
        }
    )
    service.retrieve("PAYMENT_FLAG", code_scope)
    dense_sql = dense_s.executed_sql or ""
    assert "d.source_type IN" in dense_sql
    assert "c.document_id IN" in dense_sql


def test_scope_is_mandatory_and_old_retriever_signature_fails_closed():
    service = build_service([], [], [])
    with pytest.raises(TypeError, match="RetrievalScope"):
        service.retrieve("query", None)  # type: ignore[arg-type]

    class OldRetriever(FakeRetriever):
        def search(self, query, top_k):
            return []

    service.dense_retriever = OldRetriever([])
    with pytest.raises(TypeError):
        service.retrieve("query", SCOPE)


def test_embedding_failure_is_typed_and_closes_durable_run():
    class RunSession:
        def __init__(self):
            self.row = None

        def add(self, row):
            self.row = row

        def commit(self):
            return None

        def rollback(self):
            return None

        def get(self, model, row_id):
            return self.row if self.row and self.row.id == row_id else None

    session = RunSession()

    def fail(_query):
        raise RuntimeError("provider unavailable")

    service = RetrievalService(
        dense_retriever=FakeRetriever([]),
        lexical_retriever=FakeRetriever([]),
        identifier_retriever=FakeRetriever([]),
        embedder=fail,
        session=session,
    )
    with pytest.raises(QueryEmbeddingError, match="provider failed"):
        service.retrieve("query", SCOPE)
    assert session.row.finished_at is not None
    assert session.row.fallback_reason == "pipeline_error:QueryEmbeddingError"
    assert not hasattr(session.row, "raw_query")
