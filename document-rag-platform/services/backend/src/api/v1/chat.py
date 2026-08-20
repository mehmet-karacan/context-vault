"""Chat / RAG query endpoints.

Aşama 6: ``POST /chat/query`` now runs the coordinated Aşama 5 hybrid retrieval
pipeline (``RetrievalService``) and packages the results into labeled evidence
before generating the answer (``AnswerService.generate_answer``), persisting
``message_citations`` and returning the enriched §12.4 response schema
(``answer`` / ``answerable`` / ``citations`` / optional ``retrieval_debug``).

The legacy lexical/vector keyword fallback helpers (``extract_keywords``,
``STOPWORDS``) are kept for reference but are no longer used by the main query
path. ``GET /chat/models`` is unchanged.
"""

from typing import Any, Dict, List, Optional

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...llm import (
    AVAILABLE_CHAT_MODELS,
    CHAT_MODEL,
    QUERY_INSTRUCTION,
    chat_client,
    embed_text,
)
from ...models import Chunk, Conversation, Document, Project
from src.application.answer_service import ensure_conversation, generate_answer
from src.application.retrieval_service import RetrievalService
from src.infrastructure.rate_limiter import rate_limiter
from src.infrastructure.retrieval.dense import DenseVectorRetriever
from src.infrastructure.retrieval.identifier import IdentifierRetriever
from src.infrastructure.retrieval.lexical import LexicalRetriever

router = APIRouter(tags=["chat"])


class ChatQuery(BaseModel):
    query: str
    project_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    scope: str = "all"
    model: Optional[str] = None
    debug: bool = False
    conversation_id: Optional[str] = None


def _chunk_to_dict(chunk: Chunk, doc: Optional[Document]) -> Dict[str, Any]:
    """Map an ORM Chunk (+ its Document) into the chunk shape ContextBuilder
    and AnswerService read (chunk_id / content / heading_path / locator /
    metadata)."""
    locator: Dict[str, Any] = {}
    for key in ("page_start", "page_end", "line_start", "line_end"):
        value = getattr(chunk, key, None)
        if value is not None:
            locator[key] = value
    metadata = dict(getattr(chunk, "metadata_json", None) or {})
    metadata["document_id"] = str(chunk.document_id)
    if doc is not None:
        metadata["document_name"] = doc.name
        metadata["source_type"] = doc.source_type
    return {
        "chunk_id": str(chunk.id),
        "source_id": str(chunk.document_id),
        "chunk_type": chunk.chunk_type or "document",
        "content": chunk.content or "",
        "heading_path": list(chunk.heading_path or []),
        "locator": locator,
        "content_hash": chunk.content_hash or "",
        "sequence_no": chunk.sequence_no or 0,
        "metadata": metadata,
    }


def _build_resolvers(db: Session):
    """Build DB-backed chunk / neighbour resolvers for a session."""

    def chunk_resolver(chunk_id: str) -> Optional[Dict[str, Any]]:
        row = (
            db.query(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .filter(Chunk.id == chunk_id)
            .first()
        )
        if row is None:
            return None
        chunk, doc = row
        return _chunk_to_dict(chunk, doc)

    def neighbor_resolver(source_id: str, sequence_no: int) -> Optional[Dict[str, Any]]:
        row = (
            db.query(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .filter(
                Chunk.document_id == source_id,
                Chunk.sequence_no == sequence_no,
            )
            .first()
        )
        if row is None:
            return None
        chunk, doc = row
        return _chunk_to_dict(chunk, doc)

    return chunk_resolver, neighbor_resolver


def _resolve_project_id(db: Session, project_id: Optional[str]) -> Optional[str]:
    """Return a concrete project_id for conversation persistence.

    A ``Conversation`` row requires a non-null ``project_id`` (models.py). If the
    request does not scope the query to a project, fall back to the first project
    (creating a default one if the deployment has none yet) so citation
    persistence still has a valid parent to attach to.
    """
    if project_id:
        return project_id
    project = db.query(Project).order_by(Project.created_at.asc()).first()
    if project is None:
        project = Project(id=uuid.uuid4(), name="Varsayılan", created_at=datetime.utcnow())
        db.add(project)
        db.flush()
    return str(project.id)


@router.get("/chat/models")
def list_chat_models():
    return {"models": AVAILABLE_CHAT_MODELS, "default": CHAT_MODEL}


@router.post("/chat/query")
def query_chat(
    chat_query: ChatQuery,
    _: None = Depends(rate_limiter),
    db: Session = Depends(get_db),
):
    filters: Dict[str, Any] = {}
    if chat_query.project_id:
        filters["project_id"] = chat_query.project_id
    if chat_query.document_ids:
        filters["document_ids"] = chat_query.document_ids
    if chat_query.scope:
        filters["scope"] = chat_query.scope

    chunk_resolver, neighbor_resolver = _build_resolvers(db)

    service = RetrievalService(
        dense_retriever=DenseVectorRetriever(session=db),
        lexical_retriever=LexicalRetriever(session=db),
        identifier_retriever=IdentifierRetriever(session=db),
        embedder=lambda q: embed_text(q, instruction=QUERY_INSTRUCTION),
        chunk_resolver=chunk_resolver,
        neighbor_resolver=neighbor_resolver,
    )

    retrieval_result = service.retrieve(chat_query.query, filters, debug=chat_query.debug)

    project_id = _resolve_project_id(db, chat_query.project_id)
    conversation_id = ensure_conversation(
        db, project_id=project_id, conversation_id=chat_query.conversation_id
    )

    response = generate_answer(
        query=chat_query.query,
        retrieval_result=retrieval_result,
        chunk_resolver=chunk_resolver,
        llm_client=chat_client,
        db=db,
        conversation_id=conversation_id,
        model=chat_query.model,
        debug=chat_query.debug,
    )
    return response
