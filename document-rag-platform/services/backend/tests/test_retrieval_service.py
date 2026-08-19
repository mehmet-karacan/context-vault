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

from src.application.retrieval_service import RetrievalResult, RetrievalService, dict_chunk_resolver
from src.infrastructure.retrieval.base import RetrievalCandidate
from src.infrastructure.retrieval.context_builder import ContextBuilder
from src.infrastructure.retrieval.no_answer import INTENT_DOCUMENT, INTENT_SMALLTALK
from src.infrastructure.rerankers.noop import NoopReranker


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
        return list(self._candidates[: top_k])


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
        return list(reversed(candidates))[: top_k]


def cand(chunk_id, rank, score, source, meta=None):
    return RetrievalCandidate(
        chunk_id=chunk_id, rank=rank, score=score, source=source, metadata=dict(meta or {})
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
        **kw,
    )


def test_rrf_fusion_order_is_correct():
    dense = [cand("A", 1, 0.9, "dense"), cand("B", 2, 0.8, "dense"), cand("C", 3, 0.7, "dense")]
    lexical = [cand("D", 1, 0.6, "lexical"), cand("A", 2, 0.5, "lexical")]
    identifier = [cand("E", 1, 1.0, "identifier")]
    pool = [chunk("A", "a"), chunk("B", "b"), chunk("C", "c"), chunk("D", "d"), chunk("E", "e")]

    service = build_service(dense, lexical, identifier, _pool=pool)
    result = service.retrieve("some query", debug=True)

    order = [c.chunk_id for c in result.ranked_candidates]
    # RRF(k=60): A = 1/61+1/62 highest; then D(=1/61) ties E(=1/61) -> D by id;
    # E; then B(1/62); C(1/63).
    assert order == ["A", "D", "E", "B", "C"]


def test_dedupe_removes_identical_content_copies():
    # Distinct chunk ids, identical content_hash across two different candidates
    # that both make it past fusion.
    dense = [cand("X1", 1, 0.9, "dense", meta={"content_hash": "H"}),
             cand("X2", 2, 0.8, "dense", meta={"content_hash": "H"})]
    lexical = []
    identifier = []
    pool = [chunk("X1", "identical body", content_hash="H"),
            chunk("X2", "identical body", content_hash="H")]

    service = build_service(dense, lexical, identifier, _pool=pool)
    result = service.retrieve("dup", debug=True)

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
    result = service.retrieve("q")

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
    result = service.retrieve("q")

    assert reranker.calls == 1
    # Reversing reranker flips fused order ["A","C","B"] -> ["B","C","A"].
    assert [c.chunk_id for c in result.ranked_candidates] == ["B", "C", "A"]


def test_context_built_within_budget():
    # Tight token budget (each chunk = 100 tokens via exact counter).
    dense = [cand("A", 1, 0.9, "dense"), cand("B", 2, 0.8, "dense"),
             cand("C", 3, 0.7, "dense"), cand("D", 4, 0.6, "dense")]
    lexical, identifier = [], []
    pool = [chunk("A", "aaa"), chunk("B", "bbb"), chunk("C", "ccc"), chunk("D", "ddd")]

    class Exact:
        def count(self, text):
            return 100

    builder = ContextBuilder(token_counter=Exact(), max_tokens=250, max_chunks=8)
    service = build_service(dense, lexical, identifier, _pool=pool, context_builder=builder)
    result = service.retrieve("q")

    assert result.context is not None
    assert result.context.total_tokens <= 250
    assert len(result.context.items) <= result.context.max_chunks
    assert result.context.truncated is True  # >=3 chunks (300 tokens) exceed 250


def test_answerable_document_question_classified():
    dense = [cand("A", 1, 0.9, "dense")]
    lexical, identifier = [], []
    pool = [chunk("A", "a document body")]

    service = build_service(dense, lexical, identifier, _pool=pool)
    result = service.retrieve("PAYMENT_FLAG nedir?")

    assert result.answerability is not None
    assert result.answerability.intent == INTENT_DOCUMENT
    assert result.answerability.answerable is True


def test_smalltalk_classified_as_intent_smalltalk():
    service = build_service([], [], [])
    result = service.retrieve("selam")

    assert result.answerability.intent == INTENT_SMALLTALK
    assert result.answerability.answerable is False


def test_debug_payload_contains_all_stages_with_ranks():
    dense = [cand("A", 1, 0.9, "dense"), cand("B", 2, 0.8, "dense")]
    lexical = [cand("C", 1, 0.6, "lexical")]
    identifier = [cand("B", 1, 1.0, "identifier")]
    pool = [chunk("A", "a"), chunk("B", "b"), chunk("C", "c")]

    service = build_service(dense, lexical, identifier, _pool=pool)
    result: RetrievalResult = service.retrieve("q", debug=True)

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
    result = service.retrieve("selam", debug=True)
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

    sessions = [ _RecordingSession() for _ in range(3) ]
    service = RetrievalService(
        dense_retriever=DenseVectorRetriever(session=sessions[0]),
        lexical_retriever=LexicalRetriever(session=sessions[1]),
        identifier_retriever=IdentifierRetriever(session=sessions[2]),
        chunk_resolver=None,
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

    # Non-empty: project_id only ---
    result = service.retrieve("PAYMENT_FLAG nasıl set ediliyor?", filters={"project_id": "proj-1"})
    assert result.filters == {"project_id": "proj-1"}
    for session in (dense_s, lexical_s, identifier_s):
        assert session.executed_sql is not None
        assert "d.project_id = :fp0" in session.executed_sql
        assert session.executed_params["fp0"] == "proj-1"

    # Non-empty: document_ids + scope ---
    service.retrieve(
        "PAYMENT_FLAG", filters={"document_ids": ["docA", "docB"], "scope": "code"}
    )
    dense_sql = dense_s.executed_sql or ""
    assert "d.source_type IN" in dense_sql
    assert "c.document_id IN" in dense_sql
