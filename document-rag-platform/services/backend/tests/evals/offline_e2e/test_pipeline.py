"""A9 offline E2E through production ingestion, retrieval and answer services."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.application.answer_service import ensure_conversation, generate_answer
from src.application.ingestion_orchestrator import (
    AcceptSourceCommand,
    IngestionOrchestrator,
)
from src.application.retrieval_service import RetrievalService
from src.api.v1.chat import _build_resolvers
from src.domain.clock import FixedClock
from src.domain.ingestion import SourceDescriptor
from src.domain.retrieval_scope import RetrievalScope
from src.infrastructure.chunkers.base import ChunkCandidate
from src.infrastructure.retrieval.dense import DenseVectorRetriever
from src.infrastructure.retrieval.identifier import IdentifierRetriever
from src.infrastructure.retrieval.lexical import LexicalRetriever
from src.models import (
    IngestionJob,
    MessageCitation,
    Principal,
    Project,
    Workspace,
    WorkspaceMembership,
)


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def put(self, key, data, content_type=None):
        del content_type
        self.data[key] = data
        return key

    def get(self, key):
        return self.data[key]

    def delete(self, key):
        self.data.pop(key, None)

    def list_keys(self, prefix=""):
        return sorted(key for key in self.data if key.startswith(prefix))


class DeterministicAnswerer:
    is_remote = False

    def complete_structured(self, system, user, *, schema, model=None):
        del system, user, schema, model
        return {
            "answerable": True,
            "no_answer_reason": None,
            "answer_text": "PAYMENT_FLAG değeri 1 olduğunda ödeme tamamlanmıştır.",
            "claims": [
                {
                    "claim_text": "PAYMENT_FLAG değeri 1 olduğunda ödeme tamamlanmıştır.",
                    "source_labels": ["S1"],
                }
            ],
            "used_source_labels": ["S1"],
            "uncertainty": [],
            "safety_flags": [],
        }


def _descriptor(content):
    return SourceDescriptor(
        source_type="document",
        origin="offline-e2e.txt",
        revision=None,
        content_length=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
        detected_mime="text/plain",
        declared_mime="text/plain",
        data_classification_hint="internal",
    )


@pytest.mark.integration
@pytest.mark.evals
@pytest.mark.leakage
@pytest.mark.citation
def test_production_pipeline_without_golden_derived_candidates():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    storage = MemoryStorage()
    clock = FixedClock(datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc))
    principal_id, workspace_id, project_id = (uuid.uuid4() for _ in range(3))
    try:
        db.add_all(
            [
                Principal(id=principal_id, subject=f"a9:{principal_id}"),
                Workspace(id=workspace_id, name=f"A9 {workspace_id}"),
            ]
        )
        db.flush()
        project = Project(id=project_id, workspace_id=workspace_id, name="A9 offline")
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace_id,
                    principal_id=principal_id,
                    role="admin",
                ),
                project,
            ]
        )
        db.commit()
        content = b"PAYMENT_FLAG equals 1 when the payment is complete."
        command = AcceptSourceCommand(
            project=project,
            filename="payment.txt",
            content=content,
            descriptor=_descriptor(content),
            idempotency_key=f"offline:{uuid.uuid4()}",
            actor_principal_id=principal_id,
            workspace_id=workspace_id,
        )
        orchestrator = IngestionOrchestrator(db, storage, clock=clock)
        accepted = orchestrator.accept_source(command)
        replay = orchestrator.accept_source(command)
        assert replay.replayed and replay.job_id == accepted.job_id
        orchestrator.process_job(
            accepted.job_id,
            extract_text_fn=lambda *_: content.decode(),
            chunk_text_fn=lambda *_args, **_kwargs: [
                ChunkCandidate(
                    chunk_id=str(uuid.uuid4()),
                    source_id="payment.txt",
                    chunk_type="document",
                    content=content.decode(),
                    embedding_text=content.decode(),
                    locator={"line_start": 1, "line_end": 1},
                )
            ],
            embed_texts_fn=lambda texts, instruction="": [[0.01] * 1024 for _ in texts],
            embedding_is_remote=False,
            worker_id="offline-e2e-worker",
        )
        assert db.get(IngestionJob, accepted.job_id).status == "completed"

        profile_id = project.documents[0].active_version.embedding_profile_id
        scope = RetrievalScope(
            principal_id=principal_id,
            workspace_id=workspace_id,
            project_id=project_id,
            embedding_profile_id=profile_id,
            allowed_source_types=("document",),
            data_policy="internal",
        )
        chunk_resolver, neighbor_resolver = _build_resolvers(db, scope)
        result = RetrievalService(
            dense_retriever=DenseVectorRetriever(session=db),
            lexical_retriever=LexicalRetriever(session=db),
            identifier_retriever=IdentifierRetriever(session=db),
            embedder=lambda _: [0.01] * 1024,
            chunk_resolver=chunk_resolver,
            neighbor_resolver=neighbor_resolver,
            session=db,
        ).retrieve("PAYMENT_FLAG", scope)
        assert result.answerability.answerable
        assert result.context.selected_items
        assert all(
            item.project_id == str(project_id) for item in result.context.selected_items
        )

        conversation_id = ensure_conversation(
            db,
            project_id=str(project_id),
            workspace_id=str(workspace_id),
            principal_id=str(principal_id),
        )
        response = generate_answer(
            query="PAYMENT_FLAG ne zaman tamamlanır?",
            retrieval_result=result,
            llm_client=DeterministicAnswerer(),
            db=db,
            conversation_id=conversation_id,
        )
        assert response["answerable"] is True
        assert [item["label"] for item in response["citations"]] == ["S1"]
        assert db.query(MessageCitation).count() == 1
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()
