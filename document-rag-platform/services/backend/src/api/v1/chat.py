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

from typing import Any, Dict, List, Literal, Optional

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...config import settings
from ...db import get_db
from ...llm import (
    AVAILABLE_CHAT_MODELS,
    CHAT_MODEL,
    QUERY_INSTRUCTION,
    chat_client,
    embed_text,
)
from ...models import (
    Chunk,
    ContentPolicyDecisionRecord,
    Document,
    DocumentVersion,
    EmbeddingProfile,
    Project,
)
from src.application.answer_service import (
    ConversationScopeError,
    ensure_conversation,
    generate_answer,
    load_conversation_history,
)
from src.application.retrieval_service import RetrievalService
from src.domain.identity import PrincipalContext
from src.domain.retrieval_scope import RetrievalScope
from src.domain.retrieval import ScopedNeighborKey
from src.infrastructure.security.auth import (
    get_principal_context,
    require_project_access,
)
from src.infrastructure.rate_limiter import rate_limiter
from src.infrastructure.retrieval.dense import DenseVectorRetriever
from src.infrastructure.retrieval.identifier import IdentifierRetriever
from src.infrastructure.retrieval.lexical import LexicalRetriever

router = APIRouter(tags=["chat"])


class ChatQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    project_id: UUID
    document_ids: Optional[List[UUID]] = None
    scope: Literal["all", "documents", "images", "code"] = "all"
    model: Optional[str] = None
    debug: bool = False
    conversation_id: Optional[str] = None


def _chunk_to_dict(
    chunk: Chunk,
    doc: Optional[Document],
    *,
    workspace_id: UUID,
    policy: Optional[ContentPolicyDecisionRecord] = None,
) -> Dict[str, Any]:
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
    if policy is not None:
        metadata["permit_remote_generation"] = bool(policy.permit_remote_generation)
        metadata["classification"] = policy.classification
    return {
        "chunk_id": str(chunk.id),
        "source_id": ":".join(
            (
                str(workspace_id),
                str(doc.project_id) if doc is not None else "",
                str(chunk.document_id),
                str(chunk.version_id),
                str(chunk.source_file_id or "-"),
            )
        ),
        "chunk_type": chunk.chunk_type or "document",
        "content": chunk.content or "",
        "heading_path": list(chunk.heading_path or []),
        "locator": locator,
        "content_hash": chunk.content_hash or "",
        "sequence_no": chunk.sequence_no or 0,
        "workspace_id": str(workspace_id),
        "project_id": str(doc.project_id) if doc is not None else "",
        "document_id": str(chunk.document_id),
        "version_id": str(chunk.version_id),
        "source_file_id": str(chunk.source_file_id) if chunk.source_file_id else None,
        "metadata": metadata,
        "remote_generation_allowed": bool(policy and policy.permit_remote_generation),
        "policy_classification": (
            policy.classification if policy is not None else "restricted"
        ),
    }


def _build_resolvers(db: Session, scope: RetrievalScope):
    """Build DB-backed chunk / neighbour resolvers for a session."""

    def chunk_resolver(chunk_id: str) -> Optional[Dict[str, Any]]:
        row = (
            db.query(Chunk, Document, ContentPolicyDecisionRecord)
            .join(Document, Chunk.document_id == Document.id)
            .join(Project, Document.project_id == Project.id)
            .join(DocumentVersion, Chunk.version_id == DocumentVersion.id)
            .join(
                ContentPolicyDecisionRecord,
                DocumentVersion.content_policy_decision_id
                == ContentPolicyDecisionRecord.id,
            )
            .filter(
                Chunk.id == chunk_id,
                Document.project_id == scope.project_id,
                Project.workspace_id == scope.workspace_id,
                Document.deleted_at.is_(None),
                Document.active_version_id == Chunk.version_id,
                DocumentVersion.status.in_(("ready", "completed")),
                DocumentVersion.embedding_profile_id == scope.embedding_profile_id,
                ContentPolicyDecisionRecord.permit_local_generation.is_(True),
                Document.source_type.in_(scope.allowed_source_types),
            )
            .first()
        )
        if row is None:
            return None
        chunk, doc, policy = row
        if (
            scope.allowed_document_ids is not None
            and chunk.document_id not in scope.allowed_document_ids
        ):
            return None
        return _chunk_to_dict(
            chunk, doc, workspace_id=scope.workspace_id, policy=policy
        )

    def neighbor_resolver(
        resolver_scope: RetrievalScope | None, key: ScopedNeighborKey
    ) -> Optional[Dict[str, Any]]:
        if resolver_scope != scope:
            return None
        if key.workspace_id != str(scope.workspace_id) or key.project_id != str(
            scope.project_id
        ):
            return None
        row = (
            db.query(Chunk, Document, ContentPolicyDecisionRecord)
            .join(Document, Chunk.document_id == Document.id)
            .join(Project, Document.project_id == Project.id)
            .join(DocumentVersion, Chunk.version_id == DocumentVersion.id)
            .join(
                ContentPolicyDecisionRecord,
                DocumentVersion.content_policy_decision_id
                == ContentPolicyDecisionRecord.id,
            )
            .filter(
                Chunk.document_id == key.document_id,
                Chunk.version_id == key.version_id,
                Chunk.sequence_no == key.sequence_no,
                Document.project_id == scope.project_id,
                Project.workspace_id == scope.workspace_id,
                Document.deleted_at.is_(None),
                Document.active_version_id == Chunk.version_id,
                DocumentVersion.status.in_(("ready", "completed")),
                DocumentVersion.embedding_profile_id == scope.embedding_profile_id,
                ContentPolicyDecisionRecord.permit_local_generation.is_(True),
            )
        )
        row = (
            row.filter(Chunk.source_file_id.is_(None))
            if key.source_file_id is None
            else row.filter(Chunk.source_file_id == key.source_file_id)
        ).first()
        if row is None:
            return None
        chunk, doc, policy = row
        return _chunk_to_dict(
            chunk, doc, workspace_id=scope.workspace_id, policy=policy
        )

    return chunk_resolver, neighbor_resolver


def _active_embedding_profile(db: Session) -> EmbeddingProfile:
    profile = (
        db.query(EmbeddingProfile)
        .filter(EmbeddingProfile.is_active.is_(True))
        .one_or_none()
    )
    if profile is None:
        raise HTTPException(status_code=503, detail="Retrieval profile unavailable")
    return profile


@router.get("/chat/models")
def list_chat_models():
    return {"models": AVAILABLE_CHAT_MODELS, "default": CHAT_MODEL}


@router.post("/chat/query")
def query_chat(
    chat_query: ChatQuery,
    _: None = Depends(rate_limiter),
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    require_project_access(db, principal, chat_query.project_id)
    # Validate a caller-supplied conversation before embedding or retrieval;
    # an out-of-scope identifier must not trigger provider work or leak timing.
    try:
        conversation_id = ensure_conversation(
            db,
            project_id=str(chat_query.project_id),
            workspace_id=str(principal.workspace_id),
            principal_id=str(principal.principal_id),
            conversation_id=chat_query.conversation_id,
        )
    except ConversationScopeError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    conversation_history = load_conversation_history(
        db,
        conversation_id=conversation_id,
        project_id=str(chat_query.project_id),
        workspace_id=str(principal.workspace_id),
        principal_id=str(principal.principal_id),
    )

    source_types = {
        "all": ("document", "image", "repository", "directory", "archive"),
        "documents": ("document",),
        "images": ("image",),
        "code": ("repository", "directory", "archive"),
    }[chat_query.scope]
    retrieval_scope = RetrievalScope(
        principal_id=principal.principal_id,
        workspace_id=principal.workspace_id,
        project_id=chat_query.project_id,
        allowed_document_ids=(
            tuple(chat_query.document_ids)
            if chat_query.document_ids is not None
            else None
        ),
        allowed_source_types=source_types,
        embedding_profile_id=_active_embedding_profile(db).id,
        data_policy="restricted",
    )
    debug = bool(
        chat_query.debug and principal.is_admin and settings.retrieval_debug_enabled
    )

    chunk_resolver, neighbor_resolver = _build_resolvers(db, retrieval_scope)

    service = RetrievalService(
        dense_retriever=DenseVectorRetriever(session=db),
        lexical_retriever=LexicalRetriever(session=db),
        identifier_retriever=IdentifierRetriever(session=db),
        embedder=lambda q: embed_text(q, instruction=QUERY_INSTRUCTION),
        chunk_resolver=chunk_resolver,
        neighbor_resolver=neighbor_resolver,
        session=db,
    )

    retrieval_result = service.retrieve(chat_query.query, retrieval_scope, debug=debug)

    response = generate_answer(
        query=chat_query.query,
        retrieval_result=retrieval_result,
        chunk_resolver=chunk_resolver,
        llm_client=chat_client,
        db=db,
        conversation_id=conversation_id,
        model=chat_query.model,
        debug=debug,
        conversation_history=conversation_history,
    )
    return response
