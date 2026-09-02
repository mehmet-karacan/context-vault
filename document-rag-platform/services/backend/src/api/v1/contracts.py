"""Public, content-bounded response contracts used to generate the web schema."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict
from src.domain.answer import AnswerClaim, NoAnswerReason


class ProjectResponse(BaseModel):
    id: str
    name: str
    created_at: str
    document_count: int


class DocumentResponse(BaseModel):
    id: str
    name: str
    size: int
    status: str
    uploaded_at: str
    chunks_count: int
    error_message: str | None
    project_id: str
    project_name: str | None
    active_version_id: str | None
    job_id: str | None
    job_status: str | None
    job_stage: str | None
    job_error: str | None


class UploadResponse(BaseModel):
    job_id: str
    document_id: str
    version_id: str
    status: str
    replayed: bool
    quarantine_reason: str | None
    document: DocumentResponse


class IngestionJobResponse(BaseModel):
    id: str
    version_id: str | None
    document_id: str | None
    status: str
    stage: str
    progress: int | None
    attempt: int
    error_code: str | None
    error_message: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str | None


class IngestionEventResponse(BaseModel):
    id: str
    job_id: str
    stage: str
    status: str
    message: str | None
    created_at: str | None


class CitationResponse(BaseModel):
    label: str
    document_id: str | None
    document_name: str | None
    version_id: str | None
    source_file_id: str | None
    source_type: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    file_path: str | None
    symbol_name: str | None
    line_start: int | None
    line_end: int | None
    bbox: dict[str, Any] | list[Any] | None
    snippet: str
    rank: int


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    answer: str
    answerable: bool
    no_answer_reason: NoAnswerReason | None
    claims: list[AnswerClaim]
    uncertainty: list[str]
    safety_flags: list[str]
    citations: list[CitationResponse]
    retrieval_debug: dict[str, Any] | None


class ChatModelsResponse(BaseModel):
    models: list[str]
    default: str


class SessionResponse(BaseModel):
    principal_id: str
    workspace_id: str
    roles: list[str]
    auth_mode: Literal["disabled", "api_key", "oidc"]
    upload_max_bytes: int


class DiagnosticRank(BaseModel):
    chunk_id: str
    rank: int | None
    score: float | None


class DiagnosticsResponse(BaseModel):
    retrieval_run_id: str | None
    bundle_hash: str | None
    stages: dict[str, list[DiagnosticRank]]
    fallback_reason: str | None
    timings_ms: dict[str, float]
