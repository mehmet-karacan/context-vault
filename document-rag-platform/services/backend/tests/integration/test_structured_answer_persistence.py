from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.application.answer_service import (
    ConversationScopeError,
    generate_answer,
    load_conversation_history,
)
from src.application.retrieval_service import RetrievalResult
from src.infrastructure.retrieval.base import RetrievalCandidate
from src.infrastructure.retrieval.no_answer import INTENT_DOCUMENT, Answerability
from src.models import (
    Chunk,
    ContentPolicyDecisionRecord,
    Conversation,
    Document,
    DocumentVersion,
    EmbeddingProfile,
    MessageCitation,
    Principal,
    Project,
    RetrievalRun,
    Workspace,
    WorkspaceMembership,
)


class StructuredLLM:
    is_remote = False

    def complete_structured(self, system, user, *, schema, model=None):
        del system, user, schema, model
        return {
            "answerable": True,
            "no_answer_reason": None,
            "answer_text": "Kalıcı kanıt doğrulandı.",
            "claims": [
                {
                    "claim_text": "Kalıcı kanıt doğrulandı.",
                    "source_labels": ["S1"],
                }
            ],
            "used_source_labels": ["S1"],
            "uncertainty": [],
            "safety_flags": [],
        }


@pytest.mark.integration
@pytest.mark.citation
def test_citation_snapshot_survives_source_chunk_removal():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        workspace = Workspace(name=f"answer-{uuid.uuid4()}")
        principal = Principal(subject=f"answer-{uuid.uuid4()}", is_active=True)
        db.add_all([workspace, principal])
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                principal_id=principal.id,
                role="admin",
            )
        )
        project = Project(workspace_id=workspace.id, name=f"answer-{uuid.uuid4()}")
        db.add(project)
        db.flush()
        document = Document(
            project_id=project.id,
            name="evidence.txt",
            size=32,
            status="indexed",
            source_type="document",
            data_classification="internal",
        )
        db.add(document)
        db.flush()
        policy = ContentPolicyDecisionRecord(
            document_id=document.id,
            classification="internal",
            contains_credentials=False,
            contains_private_key=False,
            contains_pii=False,
            permit_original_storage=True,
            permit_normalized_storage=True,
            permit_local_embedding=True,
            permit_remote_embedding=False,
            permit_local_generation=True,
            permit_remote_generation=False,
            redaction_required=False,
            policy_version="answer-test-v1",
            source_fingerprint=hashlib.sha256(b"answer-test").hexdigest(),
        )
        db.add(policy)
        db.flush()
        profile = db.query(EmbeddingProfile).filter_by(is_active=True).one()
        version = DocumentVersion(
            document_id=document.id,
            version_no=1,
            status="ready",
            parser_profile="test",
            chunker_profile="test",
            embedding_profile_id=profile.id,
            content_policy_decision_id=policy.id,
        )
        db.add(version)
        db.flush()
        document.active_version_id = version.id
        chunk = Chunk(
            document_id=document.id,
            version_id=version.id,
            chunk_index=0,
            sequence_no=0,
            content="Kalıcı kanıt doğrulandı.",
            content_hash=hashlib.sha256(b"persistent evidence").hexdigest(),
            search_profile="simple-websearch-v1",
        )
        run = RetrievalRun(
            principal_id=principal.id,
            workspace_id=workspace.id,
            project_id=project.id,
            embedding_profile_id=profile.id,
            query_hash=hashlib.sha256(b"query").hexdigest(),
        )
        conversation = Conversation(
            project_id=project.id,
            workspace_id=workspace.id,
            principal_id=principal.id,
            title_status="unset",
        )
        db.add_all([chunk, run, conversation])
        db.flush()

        candidate = RetrievalCandidate(
            chunk_id=str(chunk.id),
            rank=1,
            score=0.9,
            source="dense",
            metadata={
                "document_id": str(document.id),
                "version_id": str(version.id),
                "embedding_profile_id": str(profile.id),
            },
        )
        result = RetrievalResult(
            query="kanıt?",
            ranked_candidates=[candidate],
            answerability=Answerability(
                intent=INTENT_DOCUMENT,
                answerable=True,
                reason="evidence",
                evidence_count=1,
            ),
            retrieval_run_id=str(run.id),
        )
        generate_answer(
            query=result.query,
            retrieval_result=result,
            chunk_resolver=lambda _: {
                "chunk_id": str(chunk.id),
                "workspace_id": str(workspace.id),
                "project_id": str(project.id),
                "document_id": str(document.id),
                "version_id": str(version.id),
                "embedding_profile_id": str(profile.id),
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "metadata": {
                    "document_name": document.name,
                    "source_type": "document",
                    "classification": "internal",
                },
            },
            llm_client=StructuredLLM(),
            db=db,
            conversation_id=str(conversation.id),
        )
        citation = db.query(MessageCitation).one()
        original_hash = citation.evidence_hash
        snapshot = citation.evidence_snapshot_encrypted
        db.delete(chunk)
        db.flush()
        db.refresh(citation)
        assert citation.chunk_id is None
        assert citation.evidence_hash == original_hash
        assert citation.evidence_snapshot_encrypted == snapshot
        assert citation.validation_result == "valid"
        history = load_conversation_history(
            db,
            conversation_id=str(conversation.id),
            project_id=str(project.id),
            workspace_id=str(workspace.id),
            principal_id=str(principal.id),
        )
        assert [turn["role"] for turn in history] == ["user", "assistant"]
        with pytest.raises(ConversationScopeError):
            load_conversation_history(
                db,
                conversation_id=str(conversation.id),
                project_id=str(uuid.uuid4()),
                workspace_id=str(workspace.id),
                principal_id=str(principal.id),
            )
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()
