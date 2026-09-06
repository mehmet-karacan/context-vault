from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.application.retrieval_service import RetrievalService
from src.domain.retrieval import FusedHit, RerankedHit, RetrieverHit
from src.domain.retrieval_scope import RetrievalScope
from src.infrastructure.retrieval.dense import DenseVectorRetriever
from src.infrastructure.retrieval.identifier import IdentifierRetriever
from src.infrastructure.retrieval.lexical import LexicalRetriever
from src.models import EMBEDDING_DIMENSION, RetrievalRun


WORKSPACE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
PRINCIPAL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _policy(connection, policy_id, document_id, now):
    connection.execute(
        text(
            """
            INSERT INTO content_policy_decisions
              (id,document_id,classification,contains_credentials,contains_private_key,
               contains_pii,permit_original_storage,permit_normalized_storage,
               permit_local_embedding,permit_remote_embedding,permit_local_generation,
               permit_remote_generation,redaction_required,quarantine_reason,
               policy_version,source_fingerprint,created_at)
            VALUES (:id,:document,'internal',false,false,false,true,true,true,false,true,false,
                    false,NULL,'retrieval-test-v1',:fingerprint,:now)
            """
        ),
        {
            "id": policy_id,
            "document": document_id,
            "fingerprint": hashlib.sha256(str(document_id).encode()).hexdigest(),
            "now": now,
        },
    )


def _fixture(connection):
    ids = {
        name: uuid.uuid4()
        for name in (
            "project",
            "other_project",
            "document",
            "other_document",
            "active_version",
            "inactive_version",
            "other_version",
            "active_policy",
            "inactive_policy",
            "other_policy",
            "active_chunk",
            "inactive_chunk",
            "other_chunk",
            "source_file",
        )
    }
    now = datetime.now(timezone.utc)
    profile_id = connection.execute(
        text("SELECT id FROM embedding_profiles WHERE is_active")
    ).scalar_one()
    for key in ("project", "other_project"):
        connection.execute(
            text(
                "INSERT INTO projects (id,workspace_id,name,created_at,updated_at) "
                "VALUES (:id,:workspace,:name,:now,:now)"
            ),
            {
                "id": ids[key],
                "workspace": WORKSPACE_ID,
                "name": f"retrieval-{ids[key]}",
                "now": now,
            },
        )
    for doc_key, project_key in (
        ("document", "project"),
        ("other_document", "other_project"),
    ):
        connection.execute(
            text(
                """
                INSERT INTO documents
                  (id,project_id,name,size,status,source_type,data_classification,
                   uploaded_at,created_at,updated_at)
                VALUES (:id,:project,:name,10,'indexed','document','internal',:now,:now,:now)
                """
            ),
            {
                "id": ids[doc_key],
                "project": ids[project_key],
                "name": f"{doc_key}.txt",
                "now": now,
            },
        )
    _policy(connection, ids["active_policy"], ids["document"], now)
    _policy(connection, ids["inactive_policy"], ids["document"], now)
    _policy(connection, ids["other_policy"], ids["other_document"], now)
    for version_key, doc_key, policy_key, number in (
        ("active_version", "document", "active_policy", 1),
        ("inactive_version", "document", "inactive_policy", 2),
        ("other_version", "other_document", "other_policy", 1),
    ):
        connection.execute(
            text(
                """
                INSERT INTO document_versions
                  (id,document_id,version_no,status,parser_profile,chunker_profile,
                   embedding_profile_id,content_policy_decision_id,created_at)
                VALUES (:id,:document,:number,'ready','test-parser','test-chunker',
                        :profile,:policy,:now)
                """
            ),
            {
                "id": ids[version_key],
                "document": ids[doc_key],
                "number": number,
                "profile": profile_id,
                "policy": ids[policy_key],
                "now": now,
            },
        )
    connection.execute(
        text("UPDATE documents SET active_version_id=:version WHERE id=:document"),
        {"version": ids["active_version"], "document": ids["document"]},
    )
    connection.execute(
        text("UPDATE documents SET active_version_id=:version WHERE id=:document"),
        {"version": ids["other_version"], "document": ids["other_document"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO source_files
              (id,version_id,relative_path,is_binary,is_generated,is_ignored)
            VALUES (:id,:version,'src/payment_service.py',false,false,false)
            """
        ),
        {"id": ids["source_file"], "version": ids["active_version"]},
    )
    chunks = (
        (
            "active_chunk",
            "document",
            "active_version",
            "PAYMENT_FLAG active evidence",
            ids["source_file"],
        ),
        (
            "inactive_chunk",
            "document",
            "inactive_version",
            "PAYMENT_FLAG stale evidence",
            None,
        ),
        (
            "other_chunk",
            "other_document",
            "other_version",
            "PAYMENT_FLAG cross project",
            None,
        ),
    )
    for index, (chunk_key, doc_key, version_key, content, source_file) in enumerate(
        chunks
    ):
        connection.execute(
            text(
                """
                INSERT INTO chunks
                  (id,document_id,version_id,source_file_id,chunk_index,sequence_no,
                   chunk_type,content,content_hash,symbol_name,symbol_qualified_name,
                   package_name,schema_name,table_name,column_name,search_vector,
                   search_profile,identifiers,created_at)
                VALUES (:id,:document,:version,:source_file,:index,:index,'document',
                        :content,:hash,'PAYMENT_FLAG','billing.PaymentService.PAYMENT_FLAG',
                        'billing','public','payments','payment_flag',
                        to_tsvector('simple',:content),'simple-websearch-v1',
                        ARRAY['PAYMENT_FLAG'],:now)
                """
            ),
            {
                "id": ids[chunk_key],
                "document": ids[doc_key],
                "version": ids[version_key],
                "source_file": source_file,
                "index": index,
                "content": content,
                "hash": hashlib.sha256(content.encode()).hexdigest(),
                "now": now,
            },
        )
        vector = "[" + ",".join(["1"] + ["0"] * (EMBEDDING_DIMENSION - 1)) + "]"
        connection.execute(
            text(
                """
                INSERT INTO chunk_embeddings (chunk_id,embedding_profile_id,embedding,created_at)
                VALUES (:chunk,:profile,CAST(:embedding AS vector),:now)
                """
            ),
            {
                "chunk": ids[chunk_key],
                "profile": profile_id,
                "embedding": vector,
                "now": now,
            },
        )
    connection.execute(
        text("ANALYZE chunks; ANALYZE chunk_embeddings; ANALYZE source_files")
    )
    ids["profile"] = profile_id
    return ids


def _scope(ids, document_ids=None):
    return RetrievalScope(
        principal_id=PRINCIPAL_ID,
        workspace_id=WORKSPACE_ID,
        project_id=ids["project"],
        allowed_document_ids=document_ids,
        embedding_profile_id=ids["profile"],
        data_policy="internal",
    )


@pytest.mark.integration
def test_all_retrievers_are_active_profile_and_project_scoped():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    try:
        ids = _fixture(connection)
        session = Session(bind=connection)
        filters = _scope(ids).retrieval_filters()
        vector = [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)
        dense = DenseVectorRetriever(session=session).search(vector, 20, filters)
        lexical = LexicalRetriever(session=session).search("PAYMENT_FLAG", 20, filters)
        identifier = IdentifierRetriever(session=session).search(
            "PAYMENT_FLAG", 20, filters
        )
        for results in (dense, lexical, identifier):
            assert [hit.chunk_id for hit in results] == [str(ids["active_chunk"])]
            assert isinstance(results[0], RetrieverHit)
            assert results[0].project_id == str(ids["project"])
            assert results[0].embedding_profile_id == str(ids["profile"])

        empty = _scope(ids, ()).retrieval_filters()
        assert DenseVectorRetriever(session=session).search(vector, 20, empty) == []
        assert LexicalRetriever(session=session).search("PAYMENT_FLAG", 20, empty) == []
        assert (
            IdentifierRetriever(session=session).search("PAYMENT_FLAG", 20, empty) == []
        )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_pipeline_persists_content_free_run_and_immutable_provenance():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    try:
        ids = _fixture(connection)
        session = Session(bind=connection)

        def resolve(chunk_id):
            row = (
                connection.execute(
                    text(
                        """
                    SELECT c.id,c.document_id,c.version_id,c.source_file_id,c.sequence_no,
                           c.content,c.content_hash,d.project_id,p.workspace_id
                    FROM chunks c JOIN documents d ON d.id=c.document_id
                    JOIN projects p ON p.id=d.project_id WHERE c.id=:id
                    """
                    ),
                    {"id": chunk_id},
                )
                .mappings()
                .one_or_none()
            )
            return (
                dict(row)
                | {
                    "chunk_id": str(row["id"]),
                    "source_id": "scoped",
                    "chunk_type": "document",
                    "metadata": {},
                }
                if row
                else None
            )

        service = RetrievalService(
            dense_retriever=DenseVectorRetriever(session=session),
            lexical_retriever=LexicalRetriever(session=session),
            identifier_retriever=IdentifierRetriever(session=session),
            embedder=lambda query: [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1),
            chunk_resolver=resolve,
            session=session,
        )
        result = service.retrieve(
            "  PAYMENT_FLAG secret-shaped query  ", _scope(ids), debug=True
        )
        assert all(isinstance(hit, RerankedHit) for hit in result.ranked_candidates)
        assert all(
            isinstance(hit, FusedHit) for hit in result.stage_candidates["fusion"]
        )
        assert result.context is not None
        assert result.context.total_tokens <= result.context.token_budget
        run = session.get(RetrievalRun, uuid.UUID(result.retrieval_run_id))
        assert run is not None and run.finished_at is not None
        assert (
            run.query_hash
            == hashlib.sha256(b"payment_flag secret-shaped query").hexdigest()
        )
        assert "secret-shaped query" not in str(run.config_json)
        assert run.bundle_hash == result.bundle_hash
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_vector_fts_and_trigram_indexes_are_plan_eligible():
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _fixture(connection)
            connection.execute(text("SET LOCAL enable_seqscan=off"))
            vector = "[" + ",".join(["1"] + ["0"] * (EMBEDDING_DIMENSION - 1)) + "]"
            plans = {
                "ix_chunk_embeddings_embedding_hnsw": connection.execute(
                    text(
                        "EXPLAIN SELECT chunk_id FROM chunk_embeddings ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5"
                    ),
                    {"v": vector},
                )
                .scalars()
                .all(),
                "ix_chunks_search_vector_gin": connection.execute(
                    text(
                        "EXPLAIN SELECT id FROM chunks WHERE search_vector @@ websearch_to_tsquery('simple','PAYMENT_FLAG')"
                    )
                )
                .scalars()
                .all(),
                "ix_chunks_symbol_name_trgm": connection.execute(
                    text(
                        "EXPLAIN SELECT id FROM chunks WHERE lower(symbol_name) LIKE 'payment%'"
                    )
                )
                .scalars()
                .all(),
            }
            for index_name, plan in plans.items():
                assert index_name in "\n".join(plan), plan
        finally:
            transaction.rollback()
    engine.dispose()
