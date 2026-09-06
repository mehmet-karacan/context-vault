from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.domain.answer import AnswerEnvelope, NoAnswerReason, validate_grounding
from src.domain.clock import FixedClock
from src.domain.ingestion import (
    SourceDescriptor,
    decide_content_policy,
    source_fingerprint,
)
from src.domain.ingestion_state import JobStatus, transition_job
from src.domain.retrieval import ContextBundle, ContextItem, RetrieverHit
from src.domain.version_activation import (
    ConcurrentActivationError,
    activate_document_version,
)
from src.domain.work_graph import (
    InvalidWorkTransition,
    WorkStatus,
    assert_apply_admitted,
)


def test_unanswerable_grounding_without_citations_is_admitted() -> None:
    envelope = AnswerEnvelope(
        answerable=False,
        no_answer_reason=NoAnswerReason.INSUFFICIENT_EVIDENCE,
        answer_text="",
    )

    assert validate_grounding(envelope, allowed_labels=set()) is envelope


def test_ingestion_fingerprint_and_content_policy_edges() -> None:
    descriptor = SourceDescriptor(
        source_type="upload",
        origin="fixture.pdf",
        revision=None,
        content_length=7,
        content_hash="a" * 64,
        detected_mime="application/pdf",
        declared_mime=None,
        data_classification_hint="internal",
    )
    material = "\x1f".join(
        (
            "upload",
            "fixture.pdf",
            "",
            "7",
            "a" * 64,
            "application/pdf",
            "",
            "internal",
        )
    )
    assert (
        source_fingerprint(descriptor)
        == hashlib.sha256(material.encode("utf-8")).hexdigest()
    )

    safe = decide_content_policy(b"hello", classification=" PUBLIC ")
    assert safe.quarantined is False
    assert safe.permit_remote_embedding is True

    credential = decide_content_policy(b"api_key=12345678")
    assert credential.quarantined is True
    assert credential.quarantine_reason == "credential_detected"

    private_key_fixture = (
        b"-----BEGIN " + b"PRIVATE KEY-----\nfixture\n-----END " + b"PRIVATE KEY-----"
    )
    private_key = decide_content_policy(private_key_fixture)
    assert private_key.quarantine_reason == "private_key_detected"

    pii = decide_content_policy(b"owner@example.test", classification="restricted")
    assert pii.contains_pii is True
    assert pii.redaction_required is True
    assert pii.permit_remote_generation is False

    with pytest.raises(ValueError, match="unsupported data classification"):
        decide_content_policy(b"hello", classification="unknown")


def test_same_ingestion_state_is_an_idempotent_observation() -> None:
    class Job:
        status = JobStatus.QUEUED.value

    job = Job()
    transition_job(job, JobStatus.QUEUED)
    assert job.status == JobStatus.QUEUED.value


def _context_item() -> ContextItem:
    return ContextItem(
        chunk_id="chunk-1",
        workspace_id="workspace-1",
        project_id="project-1",
        document_id="document-1",
        version_id="version-1",
        source_file_id=None,
        source_id="source-1",
        chunk_type="text",
        content="bounded content",
        content_hash="b" * 64,
        evidence_hash="c" * 64,
        token_count=2,
        rank=1,
    )


def test_retrieval_value_objects_cover_validation_and_content_views() -> None:
    with pytest.raises(ValueError, match="invalid retriever source"):
        RetrieverHit(chunk_id="c", rank=1, score=1.0, source="other")
    with pytest.raises(ValueError, match="rank must be positive"):
        RetrieverHit(chunk_id="c", rank=0, score=1.0)

    hit = RetrieverHit(
        chunk_id="c",
        rank=2,
        score=0.5,
        source="lexical",
        metadata={
            "document_id": "d",
            "match_type": "exact",
            "matched_terms": ["needle"],
            "locator": {"page": 1},
        },
    )
    assert hit.retriever_name == "lexical"
    assert hit.retriever_rank == 2
    assert hit.raw_score == 0.5
    assert hit.document_id == "d"
    assert hit.match_type == "exact"
    assert hit.matched_terms == ("needle",)
    assert hit.locator == {"page": 1}

    item = _context_item()
    assert item.to_dict()["content"] == "bounded content"
    assert "content" not in item.to_dict(include_content=False)


def test_context_bundle_rejects_token_and_chunk_budget_overflow() -> None:
    item = _context_item()
    base = {
        "query_id": "query-1",
        "scope": {},
        "retrieval_run_id": "run-1",
        "embedding_profile_id": "profile-1",
        "reranker_profile": "reranker-1",
        "selected_items": (item,),
        "rejected_items": (),
        "truncated": False,
        "truncation_summary": {},
        "provenance": {},
    }
    with pytest.raises(ValueError, match="token budget"):
        ContextBundle(token_budget=1, chunk_budget=1, total_tokens=2, **base)
    with pytest.raises(ValueError, match="chunk budget"):
        ContextBundle(token_budget=2, chunk_budget=0, total_tokens=2, **base)


class _Result:
    def __init__(self, row: object | None) -> None:
        self.row = row

    def first(self) -> object | None:
        return self.row


class _Session:
    def __init__(self, row: object | None) -> None:
        self.row = row
        self.statement = ""
        self.parameters: dict[str, object] = {}

    def execute(self, statement: object, parameters: dict[str, object]) -> _Result:
        self.statement = str(statement)
        self.parameters = parameters
        return _Result(self.row)


def test_version_activation_binds_exact_compare_and_swap_parameters() -> None:
    document_id = uuid4()
    version_id = uuid4()
    expected_id = uuid4()
    instant = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    session = _Session((version_id,))

    activate_document_version(
        session,  # type: ignore[arg-type]
        document_id=document_id,
        version_id=version_id,
        expected_current_version_id=expected_id,
        clock=FixedClock(instant),
    )

    assert "IS NOT DISTINCT FROM" in session.statement
    assert session.parameters == {
        "document_id": document_id,
        "version_id": version_id,
        "expected_version_id": expected_id,
        "activated_at": instant,
    }

    losing_session = _Session(None)
    with pytest.raises(ConcurrentActivationError, match="lost compare-and-swap"):
        activate_document_version(
            losing_session,  # type: ignore[arg-type]
            document_id=document_id,
            version_id=version_id,
            expected_current_version_id=None,
            clock=FixedClock(instant),
        )


def test_work_graph_apply_rejects_non_claimed_status_first() -> None:
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    with pytest.raises(InvalidWorkTransition, match="requires a CLAIMED"):
        assert_apply_admitted(
            status=WorkStatus.READY,
            claim_status="active",
            claim_expires_at=now + timedelta(minutes=1),
            expected_fencing_token=1,
            presented_fencing_token=1,
            expected_revision="a" * 40,
            observed_revision="a" * 40,
            now=now,
        )
