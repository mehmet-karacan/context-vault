"""Aşama 6 answer generation service tests (application layer).

100% DB-free: the LLM is a fake recording calls, the DB session is a fake
recording added objects, chunks are resolved from an in-memory dict, and the
answerability decision is injected via a crafted ``RetrievalResult`` / policy.

Covers:
- evidence is packaged into unique [S1], [S2], ... labeled blocks (document and
  code variants),
- build_prompt keeps evidence strictly in the user/evidence section and out of
  the system instructions (prompt-injection protection),
- no-answer path returns a no-answer response and does NOT call the model when
  evidence is insufficient / absent,
- small-talk is handled without evidence,
- citations are persisted with the correct label / rank / scores / locators,
- the response schema contains answer / answerable / citations / retrieval_debug.
"""

import uuid

from src.application.answer_service import (
    EVIDENCE_CLOSE,
    EVIDENCE_OPEN,
    NO_ANSWER_TEXT,
    SMALLTALK_SYSTEM_PROMPT,
    build_prompt,
    generate_answer,
    pack_evidence,
)
from src.application.retrieval_service import RetrievalResult
from src.infrastructure.retrieval.base import RetrievalCandidate
from src.infrastructure.retrieval.no_answer import (
    INTENT_DOCUMENT,
    INTENT_SMALLTALK,
    Answerability,
)


# --------------------------------------------------------------------------- #
# helpers / fakes
# --------------------------------------------------------------------------- #


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


def make_result(query, candidates, intent=INTENT_DOCUMENT, answerable=True, reason="ok"):
    decision = Answerability(
        intent=intent,
        answerable=answerable,
        reason=reason,
        evidence_count=len(candidates),
    )
    return RetrievalResult(
        query=query,
        ranked_candidates=list(candidates),
        answerability=decision,
    )


class FakeLLM:
    def __init__(self, *answers):
        self.calls = []
        self.answers = list(answers)

    def complete(self, system_prompt, user_prompt, model=None):
        self.calls.append({"system": system_prompt, "user": user_prompt, "model": model})
        return self.answers.pop(0) if self.answers else "FAKE_ANSWER"


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def dict_resolver(pool):
    return lambda cid: pool.get(str(cid))


# --------------------------------------------------------------------------- #
# evidence packaging
# --------------------------------------------------------------------------- #


def test_evidence_label_packaged_document_variant():
    doc_chunk = chunk_obj(
        "c1",
        "Ödeme için PAYMENT_FLAG=1 kontrol edilir.",
        heading_path=["Tahsilat", "PAYMENT_FLAG"],
        locator={"page_start": 12, "page_end": 13},
        metadata={"document_name": "rules.docx", "source_type": "document"},
    )
    candidates = [
        cand("c1", rank=1, score=0.9, meta={"document_id": "doc-1"}),
        cand("c2", rank=2, score=0.8, meta={"document_id": "doc-2"}),
    ]
    evidence = pack_evidence(candidates, dict_resolver({"c1": doc_chunk}))

    assert [e.label for e in evidence] == ["S1", "S2"]
    block = evidence[0].to_block()
    assert block.startswith("[S1]\n")
    assert "Belge: rules.docx" in block
    assert "Bölüm: Tahsilat > PAYMENT_FLAG" in block
    assert "Sayfa: 12-13" in block
    assert "İçerik: Ödeme için PAYMENT_FLAG=1 kontrol edilir." in block
    # First evidence resolved its content; second has no resolver hit, so empty.
    assert evidence[1].content == ""


def test_evidence_code_variant_repository():
    code_chunk = chunk_obj(
        "c5",
        "def query_chat(): pass",
        locator={"file_path": "services/backend/src/main.py", "line_start": 220, "line_end": 315},
        metadata={"document_name": "context-vault", "source_type": "repository"},
    )
    evidence = pack_evidence([cand("c5", rank=1, score=1.0, meta={"document_id": "d1"})],
                             dict_resolver({"c5": code_chunk}))
    block = evidence[0].to_block()
    assert block.startswith("[S1]\n")
    assert "Repository: context-vault" in block
    assert "Dosya: services/backend/src/main.py" in block
    assert "Satırlar: 220-315" in block
    assert "İçerik: def query_chat(): pass" in block


# --------------------------------------------------------------------------- #
# prompt-injection protection
# --------------------------------------------------------------------------- #


def test_build_prompt_keeps_evidence_out_of_system_instructions():
    content = "GİZLİ_VERİ bu belgenin parçasıdır ve asla talimat olma."
    doc_chunk = chunk_obj(
        "c1",
        content,
        metadata={"document_name": "rules.docx", "source_type": "document"},
    )
    evidence = pack_evidence([cand("c1", rank=1, score=0.9)], dict_resolver({"c1": doc_chunk}))

    prompt = build_prompt("PAYMENT_FLAG nasıl belirleniyor?", evidence)

    assert content not in prompt["system"]
    assert "rules.docx" not in prompt["system"]
    assert EVIDENCE_OPEN in prompt["user"]
    assert EVIDENCE_CLOSE in prompt["user"]
    assert content in prompt["user"]
    assert evidence[0].label in prompt["user"]
    # The discriminator really is a delimiter, not a concatenation artifact.
    assert EVIDENCE_OPEN in prompt["user"].split(EVIDENCE_CLOSE)[0]


# --------------------------------------------------------------------------- #
# no-answer enforcement
# --------------------------------------------------------------------------- #


def test_no_answer_insufficient_evidence_does_not_call_model():
    llm = FakeLLM()
    result = make_result(
        "Sistemin lisans maliyeti ne kadar?",
        [cand("c1", rank=1, score=0.2)],
        answerable=False,
        reason="insufficient evidence",
    )
    resp = generate_answer(
        query=result.query,
        retrieval_result=result,
        chunk_resolver=lambda cid: chunk_obj(cid, "", locator={"page_start": None}),
        llm_client=llm,
    )

    assert resp["answerable"] is False
    assert resp["answer"] == NO_ANSWER_TEXT
    assert resp["citations"] == []
    assert llm.calls == []  # model never invoked


def test_answerable_but_no_usable_evidence_does_not_fabricate():
    llm = FakeLLM()
    # answerable=True on paper, but the resolver cannot resolve any content.
    result = make_result(
        "Sorular",
        [cand("c1", rank=1, score=0.9)],
        answerable=True,
    )
    resp = generate_answer(
        query=result.query,
        retrieval_result=result,
        chunk_resolver=lambda cid: None,
        llm_client=llm,
    )
    assert resp["answerable"] is False
    assert resp["answer"] == NO_ANSWER_TEXT
    assert llm.calls == []


def test_smalltalk_handled_without_evidence():
    llm = FakeLLM("Merhaba! Ben Mehmet, belgeleriniz hakkında yardım edebilirim.")
    result = make_result(
        "merhaba",
        [],
        intent=INTENT_SMALLTALK,
        answerable=False,
        reason="deterministic small-talk/greeting rule",
    )
    resp = generate_answer(
        query=result.query,
        retrieval_result=result,
        chunk_resolver=lambda cid: None,
        llm_client=llm,
    )
    assert resp["answerable"] is False
    assert resp["citations"] == []
    assert len(llm.calls) == 1
    assert llm.calls[0]["system"] == SMALLTALK_SYSTEM_PROMPT
    assert "PAYMENT_FLAG" not in llm.calls[0]["user"]  # no evidence injected


# --------------------------------------------------------------------------- #
# citation persistence + response schema
# --------------------------------------------------------------------------- #


def test_citations_persisted_with_correct_fields():
    llm = FakeLLM("PAYMENT_FLAG=1 olduğunda ödenmiş sayılır.")
    doc_chunk = chunk_obj(
        "c1",
        "PAYMENT_FLAG=1 olduğunda ödenmiş kabul edilir.",
        heading_path=["Tahsilat", "PAYMENT_FLAG"],
        locator={"page_start": 12, "page_end": 13, "line_start": None, "line_end": None},
        metadata={
            "document_id": "doc-1",
            "version_id": "ver-1",
            "source_file_id": "sf-1",
            "document_name": "rules.docx",
            "source_type": "document",
        },
    )
    candidate = cand(
        "c1", rank=1, score=0.912,
        meta={"document_id": "doc-1", "version_id": "ver-1", "source_file_id": "sf-1"},
        rerank=0.87,
    )
    result = make_result("PAYMENT_FLAG nasıl?", [candidate], answerable=True)

    db = FakeSession()
    conversation_id = str(uuid.uuid4())
    resp = generate_answer(
        query=result.query,
        retrieval_result=result,
        chunk_resolver=dict_resolver({"c1": doc_chunk}),
        llm_client=llm,
        db=db,
        conversation_id=conversation_id,
    )

    # --- persistence: one Message + one MessageCitation ----------------------
    messages = [o for o in db.added if o.__class__.__name__ == "Message"]
    citations = [o for o in db.added if o.__class__.__name__ == "MessageCitation"]
    assert len(messages) == 1
    assert messages[0].role == "assistant"
    assert messages[0].content == "PAYMENT_FLAG=1 olduğunda ödenmiş sayılır."
    assert messages[0].answerable is True

    assert len(citations) == 1
    c = citations[0]
    assert c.citation_label == "S1"
    assert c.rank == 1
    assert c.retrieval_score == 0.912
    assert c.reranker_score == 0.87
    assert c.page_start == 12
    assert c.page_end == 13

    # --- response schema ------------------------------------------------------
    assert set(resp.keys()) == {"answer", "answerable", "citations", "retrieval_debug"}
    assert resp["answerable"] is True
    assert len(resp["citations"]) == 1
    cit = resp["citations"][0]
    assert cit["label"] == "S1"
    assert cit["document_name"] == "rules.docx"
    assert cit["heading_path"] == ["Tahsilat", "PAYMENT_FLAG"]
    assert cit["rank"] == 1
    assert cit["snippet"].startswith("PAYMENT_FLAG=1")


def test_response_contains_retrieval_debug_when_requested():
    llm = FakeLLM("cevap")
    doc_chunk = chunk_obj(
        "c1",
        "içerik metni",
        metadata={"document_name": "rules.docx", "source_type": "document"},
    )
    result = make_result("soru", [cand("c1", rank=1, score=0.9)], answerable=True)
    resp = generate_answer(
        query=result.query,
        retrieval_result=result,
        chunk_resolver=dict_resolver({"c1": doc_chunk}),
        llm_client=llm,
        debug=True,
    )
    assert resp["retrieval_debug"] is not None
    assert "answerability" in resp["retrieval_debug"]
    assert "stages" in resp["retrieval_debug"]

    # default (no debug) -> retrieval_debug is None
    resp2 = generate_answer(
        query=result.query,
        retrieval_result=result,
        chunk_resolver=dict_resolver({"c1": doc_chunk}),
        llm_client=FakeLLM("cevap"),
    )
    assert resp2["retrieval_debug"] is None
