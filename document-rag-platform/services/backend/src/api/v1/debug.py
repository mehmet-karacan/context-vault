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

import hashlib
import uuid
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...db import get_db
from ...llm import QUERY_INSTRUCTION, embed_text
from src.api.v1.chat import _active_embedding_profile, _build_resolvers
from src.application.retrieval_service import RetrievalService
from src.config import settings
from src.domain.identity import PrincipalContext
from src.domain.retrieval_scope import RetrievalScope
from src.infrastructure.rate_limiter import rate_limiter
from src.infrastructure.observability import log_structured
from src.infrastructure.retrieval.dense import DenseVectorRetriever
from src.infrastructure.retrieval.identifier import IdentifierRetriever
from src.infrastructure.retrieval.lexical import LexicalRetriever
from src.infrastructure.security.auth import require_admin, require_project_access
from src.models import AuditEvent

router = APIRouter(tags=["debug"])


class RetrievalDebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    project_id: UUID
    document_ids: Optional[List[UUID]] = None


@router.post("/debug/retrieval")
def debug_retrieval(
    req: RetrievalDebugRequest,
    _: None = Depends(rate_limiter),
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(require_admin),
):
    """Run retrieval diagnostics without exposing raw context content."""
    if not settings.retrieval_debug_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    require_project_access(db, principal, req.project_id)
    log_structured(
        "retrieval_debug_access",
        project_id=str(req.project_id),
        query_id=hashlib.sha256(req.query.encode("utf-8")).hexdigest()[:16],
        extra_fields={
            "event": "retrieval_debug_access",
            "principal_id": str(principal.principal_id),
        },
    )
    db.add(
        AuditEvent(
            id=uuid.uuid4(),
            actor_principal_id=principal.principal_id,
            workspace_id=principal.workspace_id,
            project_id=req.project_id,
            event_type="retrieval_debug_access",
            metadata_json={
                "query_sha256_prefix": hashlib.sha256(
                    req.query.encode("utf-8")
                ).hexdigest()[:16]
            },
        )
    )
    db.commit()
    scope = RetrievalScope(
        principal_id=principal.principal_id,
        workspace_id=principal.workspace_id,
        project_id=req.project_id,
        allowed_document_ids=(
            tuple(req.document_ids) if req.document_ids is not None else None
        ),
        embedding_profile_id=_active_embedding_profile(db).id,
        data_policy="restricted",
    )

    chunk_resolver, neighbor_resolver = _build_resolvers(db, scope)

    service = RetrievalService(
        dense_retriever=DenseVectorRetriever(session=db),
        lexical_retriever=LexicalRetriever(session=db),
        identifier_retriever=IdentifierRetriever(session=db),
        embedder=lambda q: embed_text(q, instruction=QUERY_INSTRUCTION),
        chunk_resolver=chunk_resolver,
        neighbor_resolver=neighbor_resolver,
        session=db,
    )

    result = service.retrieve(req.query, scope, debug=True)
    return result.to_dict(debug=True)
