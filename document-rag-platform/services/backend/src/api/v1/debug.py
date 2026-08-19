"""Retrieval debug endpoint (Aşama 5 kabul kriteri #5).

Provides ``POST /debug/retrieval`` which runs the coordinated Aşama 5 pipeline
through :class:`RetrievalService` with **real** collaborators (DB session,
pgvector / full-text / identifier retrievers, LLM embedder) and returns the
full ``retrieval_debug`` payload: every candidate's rank / score / source
across the dense / lexical / identifier / fusion / rerank stages, plus the
final context and the no-answer / intent decision.

This is additive — it does not modify the existing chat answer-generation
(Aşama 6) — and is the debugging surface for "bütün candidate rank ve
skorlarını gösterebilir".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...llm import QUERY_INSTRUCTION, embed_text
from ...models import Chunk, Document
from src.application.retrieval_service import RetrievalService
from src.infrastructure.retrieval.dense import DenseVectorRetriever
from src.infrastructure.retrieval.identifier import IdentifierRetriever
from src.infrastructure.retrieval.lexical import LexicalRetriever

router = APIRouter(tags=["debug"])


class RetrievalDebugRequest(BaseModel):
    query: str
    project_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    scope: str = "all"
    debug: bool = True


def _chunk_to_dict(chunk: Chunk, doc: Optional[Document]) -> Dict[str, Any]:
    """Map an ORM Chunk (+ its Document) into the chunk shape ContextBuilder
    reads (chunk_id / source_id / chunk_type / content / sequence_no /
    heading_path / locator / content_hash / metadata)."""
    locator: Dict[str, Any] = {}
    for key in ("page_start", "page_end", "line_start", "line_end"):
        value = getattr(chunk, key, None)
        if value is not None:
            locator[key] = value
    metadata = dict(getattr(chunk, "metadata_json", None) or {})
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
    # Pre-fetch documents so we can attach names cheaply.
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


@router.post("/debug/retrieval")
def debug_retrieval(req: RetrievalDebugRequest, db: Session = Depends(get_db)):
    """Run the coordinated Aşama 5 retrieval and return the full debug payload."""
    filters: Dict[str, Any] = {}
    if req.project_id:
        filters["project_id"] = req.project_id
    if req.document_ids:
        filters["document_ids"] = req.document_ids
    if req.scope:
        filters["scope"] = req.scope

    chunk_resolver, neighbor_resolver = _build_resolvers(db)

    service = RetrievalService(
        dense_retriever=DenseVectorRetriever(session=db),
        lexical_retriever=LexicalRetriever(session=db),
        identifier_retriever=IdentifierRetriever(session=db),
        embedder=lambda q: embed_text(q, instruction=QUERY_INSTRUCTION),
        chunk_resolver=chunk_resolver,
        neighbor_resolver=neighbor_resolver,
    )

    result = service.retrieve(req.query, filters, debug=req.debug)
    return result.to_dict(debug=req.debug)
