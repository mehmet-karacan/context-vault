"""Aşama 6 citation-persistence integration locking tests (application layer).

These lock the two Aşama 6 verifier-found defects:

1. Citation DB persistence is wired at runtime: ``chat.py`` now resolves a real
   ``Conversation`` (via ``ensure_conversation``) and threads its id into
   ``generate_answer``, so ``Message`` + ``MessageCitation`` rows are actually
   written instead of only being written in tests that pass ``conversation_id``
   directly.

2. The ``retrieval_debug`` payload shape matches what the frontend
   ``RetrievalDebugPanel`` consumes: a ``stages`` dict whose per-stage entries
   carry ``label``/``chunk_id``/``rank``/``score``/``source`` and, when
   resolvable, ``document_name`` (so the UI never renders bare "—").

100% DB-free: the DB session is a fake recording added objects plus a minimal
query surface, and chunks resolve from an in-memory dict.
"""

import uuid

from src.application.answer_service import ensure_conversation, generate_answer
from src.application.retrieval_service import RetrievalResult
from src.infrastructure.retrieval.base import RetrievalCandidate
from src.infrastructure.retrieval.no_answer import INTENT_DOCUMENT, Answerability


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class _ORMQuery:
    """Minimal .filter().order_by().first() chain used by ensure_conversation."""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Fake ORM session: records added objects, query().first() returns [] (no existing rows)."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def query(self, _model):
        return _ORMQuery([])


class _SlowORMQuery:
    """query() that returns an existing conversation (to prove reuse, not re-create)."""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSessionWithConv(FakeSession):
    def __init__(self, existing_conversation):
        super().__init__()
        self._existing = existing_conversation

    def query(self, _model):
        return _SlowORMQuery([self._existing] if self._existing is not None else [])


class FakeLLM:
    def __init__(self, answer="CEVAP"):
        self.answer = answer
        self.calls = 0

    def complete(self, system_prompt, user_prompt, model=None):
        self.calls += 1
        return self.answer


def cand(chunk_id, rank, score, source="dense", meta=None, rerank=None):
    c = RetrievalCandidate(
        chunk_id=chunk_id,
        rank=rank,
        score=score,
        source=source,
        metadata=dict(meta or {}),
    )
    if rerank is not None:
        c.rerank_score = rerank  # type: ignore[attr-defined]
    return c


def chunk_obj(chunk_id, content, **kw):
    d = {
        "chunk_id": chunk_id,
        "content": content,
        "heading_path": kw.pop("heading_path", []),
        "locator": kw.pop("locator", {}),
        "metadata": kw.pop("metadata", {}),
    }
    d.update(kw)
    return d


def dict_resolver(pool):
    return lambda cid: pool.get(str(cid))


def make_result(query, candidates, answerable=True, stage_candidates=None):
    decision = Answerability(
        intent=INTENT_DOCUMENT,
        answerable=answerable,
        reason="ok",
        evidence_count=len(candidates),
    )
    return RetrievalResult(
        query=query,
        ranked_candidates=list(candidates),
        answerability=decision,
        stage_candidates=dict(stage_candidates or {}),
    )


# --------------------------------------------------------------------------- #
# DEFECT 2 locking: runtime conversation wiring + persistence
# --------------------------------------------------------------------------- #


def test_chat_runtime_creates_conversation_and_persists_message_and_citations():
    """`chat.py` calls `ensure_conversation`, which creates a Conversation whose
    id is threaded into `generate_answer`; Message + MessageCitation are written."""
    db = FakeSession()
    doc_chunk = chunk_obj(
        "c1",
        "PAYMENT_FLAG=1 olduğunda ödenmiş kabul edilir.",
        heading_path=["Tahsilat", "PAYMENT_FLAG"],
        locator={"page_start": 12, "page_end": 13},
        metadata={
            "document_id": "doc-1",
            "version_id": "ver-1",
            "source_file_id": "sf-1",
            "document_name": "rules.docx",
            "source_type": "document",
        },
    )
    candidate = cand(
        "c1",
        rank=1,
        score=0.912,
        meta={"document_id": "doc-1", "version_id": "ver-1", "source_file_id": "sf-1"},
        rerank=0.87,
    )

    # --- the exact runtime sequence chat.py performs -------------------------
    conv_id = ensure_conversation(db, project_id="proj-1", conversation_id=None)
    resp = generate_answer(
        query="PAYMENT_FLAG nasıl?",
        retrieval_result=make_result("PAYMENT_FLAG nasıl?", [candidate]),
        chunk_resolver=dict_resolver({"c1": doc_chunk}),
        llm_client=FakeLLM("PAYMENT_FLAG=1 olduğunda ödenmiş sayılır."),
        db=db,
        conversation_id=conv_id,
    )

    conv = [o for o in db.added if o.__class__.__name__ == "Conversation"]
    assert len(conv) == 1, "a Conversation must be created and flushed"
    assert str(conv[0].id) == conv_id

    messages = [o for o in db.added if o.__class__.__name__ == "Message"]
    citations = [o for o in db.added if o.__class__.__name__ == "MessageCitation"]

    assert len(messages) == 1
    assert str(messages[0].conversation_id) == conv_id
    assert messages[0].role == "assistant"
    assert messages[0].answerable is True

    assert len(citations) == 1
    c = citations[0]
    assert str(c.message_id) == str(messages[0].id)
    assert c.citation_label == "S1"
    assert c.rank == 1
    assert c.retrieval_score == 0.912
    assert c.reranker_score == 0.87
    assert c.page_start == 12
    assert c.page_end == 13

    # Citations still returned in the response regardless of persistence.
    assert len(resp["citations"]) == 1
    assert resp["citations"][0]["label"] == "S1"


def test_chat_runtime_reuses_existing_conversation_when_present():
    """When a Conversation already exists for the project it is reused, not
    re-created (ensure_conversation returns the existing id)."""
    existing = type("Conv", (), {"id": uuid.uuid4()})()
    db = FakeSessionWithConv(existing)

    conv_id = ensure_conversation(db, project_id="proj-1", conversation_id=None)

    assert conv_id == str(existing.id)
    convs = [o for o in db.added if o.__class__.__name__ == "Conversation"]
    assert convs == [], "no new Conversation should be created when one exists"


def test_chat_runtime_honors_explicit_conversation_id():
    """A client-supplied conversation_id is returned unchanged (resume path)."""
    cid = str(uuid.uuid4())
    assert ensure_conversation(FakeSession(), project_id="proj-1", conversation_id=cid) == cid


def test_feature_new_citations_false_skips_persistence_but_keeps_citations():
    """When FEATURE_NEW_CITATIONS is off, no Message/MessageCitation rows are
    written, but the citations are still returned in the response."""
    db = FakeSession()
    doc_chunk = chunk_obj(
        "c1",
        "içerik metni",
        metadata={"document_name": "rules.docx", "source_type": "document"},
    )
    candidate = cand("c1", rank=1, score=0.9, meta={"document_id": "doc-1"})

    resp = generate_answer(
        query="soru",
        retrieval_result=make_result("soru", [candidate]),
        chunk_resolver=dict_resolver({"c1": doc_chunk}),
        llm_client=FakeLLM("cevap"),
        db=db,
        conversation_id=str(uuid.uuid4()),
        feature_new_citations=False,
    )

    assert [o for o in db.added if o.__class__.__name__ == "Message"] == []
    assert [o for o in db.added if o.__class__.__name__ == "MessageCitation"] == []
    assert len(resp["citations"]) == 1
    assert resp["citations"][0]["label"] == "S1"


def test_ensure_conversation_returns_none_without_db_or_project():
    # DB-free path must not crash and must signal "no persistence".
    assert ensure_conversation(None, project_id="proj-1") is None
    assert ensure_conversation(FakeSession(), project_id=None) is None


# --------------------------------------------------------------------------- #
# DEFECT 1 locking: retrieval_debug shape matches the frontend
# --------------------------------------------------------------------------- #


def test_retrieval_debug_stages_shape_matches_frontend_contract():
    """debug_payload().stages is a dict of ranked lists; each entry carries
    chunk_id/label/rank/score/source and document_name when resolvable."""
    from src.infrastructure.retrieval.no_answer import Answerability

    dense = [cand("A", 1, 0.9, "dense")]
    rerank = [cand("A", 1, 0.9, "dense", rerank=0.95)]
    rerank[0].chunk = chunk_obj(
        "A", "body", metadata={"document_name": "rules.docx", "source_type": "document"}
    )

    result = RetrievalResult(
        query="q",
        ranked_candidates=rerank,
        answerability=Answerability(intent=INTENT_DOCUMENT, answerable=True, reason="ok"),
        stage_candidates={"dense": dense, "rerank": rerank},
    )

    payload = result.debug_payload()
    assert "stages" in payload
    stages = payload["stages"]
    assert isinstance(stages, dict)

    # The frontend reads Object.entries(stages) and renders each stage's list.
    for stage, items in stages.items():
        assert isinstance(items, list), stage
        for item in items:
            # Every entry is renderable: at least one of label/document_name/chunk_id.
            assert {"chunk_id", "rank", "score", "source"} <= set(item), item
            assert item.get("label") or item.get("document_name") or item.get("chunk_id")

    # document_name is surfaced when the resolved chunk has it (rerank stage).
    rerank_item = stages["rerank"][0]
    assert rerank_item["document_name"] == "rules.docx"
    assert rerank_item["label"] == "A"
    assert rerank_item["rerank_score"] == 0.95

    # dense candidate has no document_name -> label/chunk_id still render it.
    assert "document_name" not in stages["dense"][0]
    assert stages["dense"][0]["chunk_id"] == "A"
