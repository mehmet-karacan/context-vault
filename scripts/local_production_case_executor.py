#!/usr/bin/env python3
"""Concrete production-core executor for the trusted-local A9 benchmark.

The module is loaded only inside the admitted benchmark child.  It refuses a
non-empty PostgreSQL application schema or MinIO bucket, installs an exact
local-BGE embedding profile, and then invokes the production ingestion,
retrieval, answer, and persistence services.  It never cleans or rewrites an
existing target; benchmark evidence remains in the dedicated ``cv3_eval_*`` DB
and ``cv3-eval-*`` bucket for operator review.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping


REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "document-rag-platform/services/backend"
EXPECTED_ALEMBIC_HEAD = "cv3_00000006"
EMBEDDING_PROVIDER = "local-sentence-transformers"
BGE_IDENTITY = "BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181"
QWEN_IDENTITY = "Qwen/Qwen2.5-1.5B-Instruct@989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
QUERY_INSTRUCTION = "Bu soruyu ilgili belge parçalarını bulmak için temsil et: "
PASSAGE_INSTRUCTION = "Bu metni bir belge arama sisteminde bulunmak üzere temsil et: "
PROFILE_VERSION = 2
LEGACY_PRINCIPAL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
LEGACY_WORKSPACE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")

PROVENANCE = {
    "pipeline": "context-vault-production-core",
    "ingestion": "IngestionOrchestrator.accept_source/process_job",
    "retrieval": "RetrievalService.retrieve",
    "answer": "application.answer_service.generate_answer",
    "celery_delivery_exercised": False,
    "redis_role": "health-only",
}

SOURCE_TYPES_BY_SCOPE = {
    "all": ("document", "image", "repository", "directory", "archive"),
    "documents": ("document",),
    "images": ("image",),
    "code": ("repository", "directory", "archive"),
}
CLASSIFICATION_RANK = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


class LocalProductionExecutorError(RuntimeError):
    """The isolated production-core execution contract failed closed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _domain_hash(domain: str, value: Any) -> str:
    digest = hashlib.sha256((domain + "\0").encode("utf-8"))
    digest.update(_canonical(value))
    return digest.hexdigest()


def _stable_uuid(execution_sha256: str, *parts: str) -> uuid.UUID:
    material = ":".join((execution_sha256, *parts))
    return uuid.uuid5(uuid.NAMESPACE_URL, f"context-vault:a9:{material}")


def _role(persona: str) -> str:
    aliases = {
        "admin": "admin",
        "project-admin": "admin",
        "member": "member",
        "project-member": "member",
        "reader": "reader",
        "project-reader": "reader",
    }
    try:
        return aliases[persona]
    except KeyError as exc:
        raise LocalProductionExecutorError(
            "permission persona is not supported by the production executor"
        ) from exc


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _candidate_value(candidate: Any, name: str) -> Any:
    direct = getattr(candidate, name, None)
    if direct is not None:
        return direct
    fused = getattr(candidate, "fused_hit", None)
    representative = getattr(fused, "representative", None)
    return getattr(representative, name, None)


def _assert_exact_migration_seed(
    *,
    principals: list[tuple[Any, ...]],
    workspaces: list[tuple[Any, ...]],
    memberships: list[tuple[Any, ...]],
) -> None:
    if principals != [
        (
            LEGACY_PRINCIPAL_ID,
            "migration:legacy-owner",
            "Legacy data owner (inactive)",
            False,
        )
    ]:
        raise LocalProductionExecutorError(
            "isolated database principal seed is not exact"
        )
    if workspaces != [(LEGACY_WORKSPACE_ID, "Legacy migrated workspace")]:
        raise LocalProductionExecutorError(
            "isolated database workspace seed is not exact"
        )
    if memberships != [(LEGACY_WORKSPACE_ID, LEGACY_PRINCIPAL_ID, "admin")]:
        raise LocalProductionExecutorError(
            "isolated database membership seed is not exact"
        )


def _expected_storage_keys(rows: list[tuple[str, str]]) -> set[str]:
    valid_statuses = {"staged", "referenced", "quarantined", "deleted"}
    if any(
        not isinstance(key, str) or not key or status not in valid_statuses
        for key, status in rows
    ):
        raise LocalProductionExecutorError("storage registry row is malformed")
    live = {key for key, status in rows if status != "deleted"}
    deleted = {key for key, status in rows if status == "deleted"}
    if live & deleted:
        raise LocalProductionExecutorError("storage registry status is ambiguous")
    return live


def _supported_claim_count(
    claims: list[Mapping[str, Any]], citations: list[Mapping[str, Any]]
) -> int:
    snippets = {
        citation.get("label"): " ".join(
            str(citation.get("snippet") or "").casefold().split()
        )
        for citation in citations
        if isinstance(citation.get("label"), str)
    }
    supported = 0
    for claim in claims:
        text = " ".join(str(claim.get("claim_text") or "").casefold().split())
        labels = claim.get("source_labels")
        if (
            text
            and isinstance(labels, (list, tuple))
            and labels
            and all(
                isinstance(label, str) and text in snippets.get(label, "")
                for label in labels
            )
        ):
            supported += 1
    return supported


class _ProductionRuntime:
    """State shared by all cases in one admitted runner process."""

    def __init__(
        self,
        config: Mapping[str, Any],
        embedding_provider: Any,
        generation_client: Any,
    ) -> None:
        self.config = config
        self.embedding_provider = embedding_provider
        self.generation_client = generation_client
        self.db: Any = None
        self.engine: Any = None
        self.storage: Any = None
        self.redis: Any = None
        self.profile: Any = None
        self._modules: dict[str, Any] = {}
        self._workspaces: dict[str, Any] = {}
        self._projects: dict[tuple[str, str], Any] = {}
        self._principals: dict[tuple[str, str, str], Any] = {}
        self._prepared = False
        self._finalized = False
        self._attestation: dict[str, str] | None = None

    def _load_backend(self) -> None:
        # These fixed child-only values prevent construction of a usable remote
        # provider and keep the answer envelope inside Qwen's admitted window.
        os.environ["LITELLM_API_KEY"] = "local-eval-remote-provider-disabled"
        os.environ["ANSWER_CONTEXT_WINDOW_TOKENS"] = "4096"
        os.environ["ANSWER_RESERVED_OUTPUT_TOKENS"] = "64"
        os.environ["ANSWER_SAFETY_MARGIN_TOKENS"] = "256"
        os.environ["CONTEXT_MAX_TOKENS"] = "3000"
        if str(BACKEND) not in sys.path:
            sys.path.insert(0, str(BACKEND))

        try:
            import redis
            from sqlalchemy import create_engine, func, select, text
            from sqlalchemy.orm import Session

            from src.application.answer_service import (
                RAG_SYSTEM_PROMPT,
                ensure_conversation,
                generate_answer,
            )
            from src.application.ingestion_orchestrator import (
                AcceptSourceCommand,
                IngestionOrchestrator,
            )
            from src.application.retrieval_service import RetrievalService
            from src.api.v1.chat import _build_resolvers
            from src.config import settings
            from src.domain.answer import AnswerEnvelope
            from src.domain.identity import PrincipalContext
            from src.domain.ingestion import SourceDescriptor
            from src.domain.retrieval_scope import RetrievalScope
            from src.infrastructure.embeddings.cache import profile_config_hash
            from src.infrastructure.retrieval.dense import DenseVectorRetriever
            from src.infrastructure.retrieval.identifier import IdentifierRetriever
            from src.infrastructure.retrieval.lexical import LexicalRetriever
            from src.infrastructure.security.auth import require_project_access
            from src.infrastructure.storage.minio_storage import MinioObjectStorage
            from src.models import (
                EmbeddingProfile,
                IngestionAttempt,
                Principal,
                Project,
                StorageObject,
                Workspace,
                WorkspaceMembership,
            )
            from src.persistence import Base
        except Exception as exc:  # noqa: BLE001 - locked backend import boundary
            raise LocalProductionExecutorError(
                "locked production runtime could not be imported"
            ) from exc

        self._modules = locals()
        self._modules.update(
            {
                "redis_module": redis,
                "create_engine": create_engine,
                "func": func,
                "select": select,
                "text": text,
                "Session": Session,
                "Base": Base,
                "settings": settings,
                "AnswerEnvelope": AnswerEnvelope,
                "RAG_SYSTEM_PROMPT": RAG_SYSTEM_PROMPT,
                "ensure_conversation": ensure_conversation,
                "generate_answer": generate_answer,
                "AcceptSourceCommand": AcceptSourceCommand,
                "IngestionOrchestrator": IngestionOrchestrator,
                "RetrievalService": RetrievalService,
                "_build_resolvers": _build_resolvers,
                "PrincipalContext": PrincipalContext,
                "SourceDescriptor": SourceDescriptor,
                "RetrievalScope": RetrievalScope,
                "profile_config_hash": profile_config_hash,
                "DenseVectorRetriever": DenseVectorRetriever,
                "IdentifierRetriever": IdentifierRetriever,
                "LexicalRetriever": LexicalRetriever,
                "require_project_access": require_project_access,
                "MinioObjectStorage": MinioObjectStorage,
                "EmbeddingProfile": EmbeddingProfile,
                "IngestionAttempt": IngestionAttempt,
                "Principal": Principal,
                "Project": Project,
                "StorageObject": StorageObject,
                "Workspace": Workspace,
                "WorkspaceMembership": WorkspaceMembership,
            }
        )

    def prepare(self) -> None:
        if self._prepared or self._finalized:
            raise LocalProductionExecutorError(
                "production runtime lifecycle is invalid"
            )
        self._load_backend()
        env = self.config["env"]
        create_engine = self._modules["create_engine"]
        Session = self._modules["Session"]
        text = self._modules["text"]
        select = self._modules["select"]
        func = self._modules["func"]

        try:
            self.redis = self._modules["redis_module"].Redis.from_url(
                env["REDIS_URL"],
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            if self.redis.ping() is not True:
                raise LocalProductionExecutorError("Redis health check failed")

            self.engine = create_engine(env["DATABASE_URL"], pool_pre_ping=True)
            self.db = Session(bind=self.engine)
            database_name = self.db.execute(text("SELECT current_database()"))
            if database_name.scalar_one() != self.config["database_name"]:
                raise LocalProductionExecutorError(
                    "connected database does not match admitted isolated name"
                )
            heads = (
                self.db.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
            if heads != [EXPECTED_ALEMBIC_HEAD]:
                raise LocalProductionExecutorError(
                    "isolated database is not at the exact Alembic head"
                )

            Base = self._modules["Base"]
            seeded_tables = {
                "principals",
                "workspaces",
                "workspace_memberships",
                "embedding_profiles",
            }
            for table in Base.metadata.sorted_tables:
                if table.name in seeded_tables:
                    continue
                count = self.db.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
                if count != 0:
                    raise LocalProductionExecutorError(
                        "isolated database contains pre-existing application data"
                    )
            Principal = self._modules["Principal"]
            Workspace = self._modules["Workspace"]
            WorkspaceMembership = self._modules["WorkspaceMembership"]
            _assert_exact_migration_seed(
                principals=self.db.query(
                    Principal.id,
                    Principal.subject,
                    Principal.display_name,
                    Principal.is_active,
                ).all(),
                workspaces=self.db.query(Workspace.id, Workspace.name).all(),
                memberships=self.db.query(
                    WorkspaceMembership.workspace_id,
                    WorkspaceMembership.principal_id,
                    WorkspaceMembership.role,
                ).all(),
            )

            storage_type = self._modules["MinioObjectStorage"]
            self.storage = storage_type(
                endpoint=env["MINIO_ENDPOINT"],
                access_key=env["MINIO_ACCESS_KEY"],
                secret_key=env["MINIO_SECRET_KEY"],
                bucket=env["MINIO_BUCKET"],
                encryption_key=env["OBJECT_STORAGE_ENCRYPTION_KEY"],
                allow_legacy_plaintext_reads=False,
            )
            if self.storage.list_keys():
                raise LocalProductionExecutorError(
                    "isolated MinIO bucket contains pre-existing objects"
                )
            self._install_local_profile()
            self._attestation = self._build_attestation()
            self._prepared = True
        except Exception:
            self._close_resources()
            raise

    def _install_local_profile(self) -> None:
        EmbeddingProfile = self._modules["EmbeddingProfile"]
        profile_config_hash = self._modules["profile_config_hash"]
        active = (
            self.db.query(EmbeddingProfile)
            .filter(EmbeddingProfile.is_active.is_(True))
            .one_or_none()
        )
        if active is None or self.db.query(EmbeddingProfile).count() != 1:
            raise LocalProductionExecutorError(
                "isolated database lacks its exact migration-seeded active profile"
            )
        active.is_active = False
        self.db.flush([active])
        profile = EmbeddingProfile(
            id=_stable_uuid(self.config["execution_sha256"], "embedding-profile"),
            provider=EMBEDDING_PROVIDER,
            model=BGE_IDENTITY,
            dimension=1024,
            distance_metric="cosine",
            query_prefix=QUERY_INSTRUCTION,
            passage_prefix=PASSAGE_INSTRUCTION,
            profile_version=PROFILE_VERSION,
            config_hash="pending",
            is_active=True,
        )
        profile.config_hash = profile_config_hash(profile)
        self.db.add(profile)
        self.db.commit()
        self.profile = profile

    def _build_attestation(self) -> dict[str, str]:
        settings = self._modules["settings"]
        AnswerEnvelope = self._modules["AnswerEnvelope"]
        prompt_contract = {
            "template_version": settings.ANSWER_PROMPT_TEMPLATE_VERSION,
            "system_prompt_sha256": hashlib.sha256(
                self._modules["RAG_SYSTEM_PROMPT"].encode("utf-8")
            ).hexdigest(),
            "answer_schema_sha256": hashlib.sha256(
                _canonical(AnswerEnvelope.json_schema_contract())
            ).hexdigest(),
            "generation_model": QWEN_IDENTITY,
        }
        pipeline_config = {
            "parser_profile": "parser-router-v1",
            "chunker_profile": "context-vault-chunker-v4",
            "embedding_profile_hash": self.profile.config_hash,
            "context_max_chunks": settings.CONTEXT_MAX_CHUNKS,
            "context_max_tokens": settings.CONTEXT_MAX_TOKENS,
            "vector_candidate_k": settings.VECTOR_CANDIDATE_K,
            "lexical_candidate_k": settings.LEXICAL_CANDIDATE_K,
            "identifier_candidate_k": settings.IDENTIFIER_CANDIDATE_K,
            "fusion_candidate_k": settings.FUSION_CANDIDATE_K,
            "rrf_k": settings.RRF_K,
            "reranker_enabled": False,
            "answer_context_window_tokens": settings.ANSWER_CONTEXT_WINDOW_TOKENS,
            "answer_reserved_output_tokens": settings.ANSWER_RESERVED_OUTPUT_TOKENS,
            "answer_safety_margin_tokens": settings.ANSWER_SAFETY_MARGIN_TOKENS,
            "celery_delivery_exercised": False,
            "redis_role": "health-only",
            "model_device": "mps",
        }
        return {
            "embedding_profile_hash": self.profile.config_hash,
            "prompt_hash": _domain_hash(
                "context-vault/production-answer-prompt/v1", prompt_contract
            ),
            "pipeline_config_hash": _domain_hash(
                "context-vault/production-pipeline-config/v1", pipeline_config
            ),
        }

    def attestation(self) -> dict[str, str]:
        if not self._prepared or self._attestation is None:
            raise LocalProductionExecutorError(
                "production runtime attestation is unavailable"
            )
        return dict(self._attestation)

    def _identity(self, case: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
        Workspace = self._modules["Workspace"]
        Project = self._modules["Project"]
        Principal = self._modules["Principal"]
        WorkspaceMembership = self._modules["WorkspaceMembership"]
        PrincipalContext = self._modules["PrincipalContext"]
        execution = self.config["execution_sha256"]
        workspace_key = case["workspace_fixture"]
        project_key = case["project_fixture"]
        persona = case["permission_persona"]

        workspace = self._workspaces.get(workspace_key)
        if workspace is None:
            workspace = Workspace(
                id=_stable_uuid(execution, "workspace", workspace_key),
                name=f"A9 isolated {workspace_key}",
            )
            self.db.add(workspace)
            self.db.flush([workspace])
            self._workspaces[workspace_key] = workspace

        project_cache_key = (workspace_key, project_key)
        project = self._projects.get(project_cache_key)
        if project is None:
            project = Project(
                id=_stable_uuid(execution, "project", workspace_key, project_key),
                workspace_id=workspace.id,
                name=f"A9 isolated {project_key}",
            )
            self.db.add(project)
            self.db.flush([project])
            self._projects[project_cache_key] = project

        principal_cache_key = (workspace_key, project_key, persona)
        principal = self._principals.get(principal_cache_key)
        if principal is None:
            principal = Principal(
                id=_stable_uuid(
                    execution, "principal", workspace_key, project_key, persona
                ),
                subject=(
                    "a9-local:"
                    + hashlib.sha256(
                        ":".join(principal_cache_key).encode("utf-8")
                    ).hexdigest()
                ),
                is_active=True,
            )
            self.db.add(principal)
            self.db.flush([principal])
            self.db.add(
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    principal_id=principal.id,
                    role=_role(persona),
                )
            )
            self.db.flush()
            self._principals[principal_cache_key] = principal
        self.db.commit()
        context = PrincipalContext(
            principal_id=principal.id,
            workspace_id=workspace.id,
            roles=frozenset({_role(persona)}),
            auth_mode="local-benchmark",
        )
        return principal, workspace, project, context

    def execute_case(self, case: Mapping[str, Any]) -> dict[str, Any]:
        if not self._prepared or self._finalized:
            raise LocalProductionExecutorError(
                "production runtime is not available for case execution"
            )
        case_started = time.perf_counter()
        principal, workspace, project, principal_context = self._identity(case)
        self._modules["require_project_access"](self.db, principal_context, project.id)
        IngestionOrchestrator = self._modules["IngestionOrchestrator"]
        AcceptSourceCommand = self._modules["AcceptSourceCommand"]
        SourceDescriptor = self._modules["SourceDescriptor"]
        IngestionAttempt = self._modules["IngestionAttempt"]
        StorageObject = self._modules["StorageObject"]

        ingestion_started = time.perf_counter()
        document_ids: dict[str, uuid.UUID] = {}
        version_ids: dict[str, uuid.UUID] = {}
        job_ids: list[uuid.UUID] = []
        orchestrator = IngestionOrchestrator(self.db, self.storage)
        for document in case["documents"]:
            try:
                content = base64.b64decode(document["content_base64"], validate=True)
            except Exception as exc:  # noqa: BLE001 - admitted pack invariant
                raise LocalProductionExecutorError(
                    "admitted document bytes could not be decoded"
                ) from exc
            descriptor = SourceDescriptor(
                source_type=document["source_type"],
                origin=document["filename"],
                revision=None,
                content_length=len(content),
                content_hash=hashlib.sha256(content).hexdigest(),
                detected_mime=document["mime_type"],
                declared_mime=document["mime_type"],
                data_classification_hint=document["classification"],
            )
            accepted = orchestrator.accept_source(
                AcceptSourceCommand(
                    project=project,
                    filename=document["filename"],
                    content=content,
                    descriptor=descriptor,
                    idempotency_key=(
                        f"a9:{self.config['execution_sha256']}:{case['id']}:"
                        f"{document['document_id']}"
                    ),
                    actor_principal_id=principal.id,
                    workspace_id=workspace.id,
                )
            )
            if accepted.status == "failed" or accepted.quarantine_reason:
                raise LocalProductionExecutorError(
                    "production content policy rejected an admitted benchmark source"
                )
            result = orchestrator.process_job(
                accepted.job_id,
                embed_texts_fn=self.embedding_provider.embed,
                embedding_is_remote=False,
                worker_id=f"a9-local-{case['id']}",
            )
            if result.get("status") != "completed":
                raise LocalProductionExecutorError(
                    "production ingestion did not reach completed state"
                )
            document_ids[document["document_id"]] = accepted.document_id
            version_ids[document["document_id"]] = accepted.version_id
            job_ids.append(accepted.job_id)
        ingestion_ms = (time.perf_counter() - ingestion_started) * 1000

        scope_name = case["scope"]
        source_types = SOURCE_TYPES_BY_SCOPE[scope_name]
        classifications = [document["classification"] for document in case["documents"]]
        data_policy = max(classifications, key=CLASSIFICATION_RANK.__getitem__)
        RetrievalScope = self._modules["RetrievalScope"]
        retrieval_scope = RetrievalScope(
            principal_id=principal.id,
            workspace_id=workspace.id,
            project_id=project.id,
            allowed_document_ids=tuple(document_ids.values()),
            allowed_source_types=source_types,
            embedding_profile_id=self.profile.id,
            data_policy=data_policy,
        )
        chunk_resolver, neighbor_resolver = self._modules["_build_resolvers"](
            self.db, retrieval_scope
        )
        RetrievalService = self._modules["RetrievalService"]
        service = RetrievalService(
            dense_retriever=self._modules["DenseVectorRetriever"](session=self.db),
            lexical_retriever=self._modules["LexicalRetriever"](session=self.db),
            identifier_retriever=self._modules["IdentifierRetriever"](session=self.db),
            embedder=self.embedding_provider.embed_one,
            chunk_resolver=chunk_resolver,
            neighbor_resolver=neighbor_resolver,
            session=self.db,
        )
        retrieval_started = time.perf_counter()
        retrieval = service.retrieve(case["query"], retrieval_scope, debug=False)
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000

        answer_started = time.perf_counter()
        conversation_id = self._modules["ensure_conversation"](
            self.db,
            project_id=str(project.id),
            workspace_id=str(workspace.id),
            principal_id=str(principal.id),
        )
        response = self._modules["generate_answer"](
            query=case["query"],
            retrieval_result=retrieval,
            chunk_resolver=chunk_resolver,
            llm_client=self.generation_client,
            db=self.db,
            conversation_id=conversation_id,
            model=QWEN_IDENTITY,
            debug=False,
            feature_new_citations=True,
        )
        self.db.commit()
        answer_ms = (time.perf_counter() - answer_started) * 1000

        reverse_documents = {
            str(actual): logical for logical, actual in document_ids.items()
        }
        retrieved_actual: list[str] = []
        active_version_leakage = 0
        profile_leakage = 0
        cross_project_leakage = 0
        cross_workspace_leakage = 0
        chunk_ids: list[str] = []
        for candidate in retrieval.ranked_candidates:
            chunk_ids.append(str(_candidate_value(candidate, "chunk_id") or ""))
            actual_document = str(_candidate_value(candidate, "document_id") or "")
            if actual_document:
                retrieved_actual.append(actual_document)
            logical = reverse_documents.get(actual_document)
            actual_version = str(_candidate_value(candidate, "version_id") or "")
            expected_version = str(version_ids.get(logical, ""))
            active_version_leakage += int(
                logical is None or actual_version != expected_version
            )
            profile_leakage += int(
                str(_candidate_value(candidate, "embedding_profile_id") or "")
                != str(self.profile.id)
            )
            cross_project_leakage += int(
                str(_candidate_value(candidate, "project_id") or "") != str(project.id)
            )
            cross_workspace_leakage += int(
                str(_candidate_value(candidate, "workspace_id") or "")
                != str(workspace.id)
            )
        retrieved = _dedupe(
            [
                reverse_documents[actual]
                for actual in retrieved_actual
                if actual in reverse_documents
            ]
        )
        cited = _dedupe(
            [
                reverse_documents[str(citation.get("document_id"))]
                for citation in response.get("citations", [])
                if str(citation.get("document_id")) in reverse_documents
            ]
        )
        duplicate_count = len(chunk_ids) - len(set(chunk_ids))
        retry_count = max(
            0,
            self.db.query(IngestionAttempt)
            .filter(IngestionAttempt.job_id.in_(job_ids))
            .count()
            - len(job_ids),
        )
        registered = {key for (key,) in self.db.query(StorageObject.storage_key).all()}
        orphan_count = len(set(self.storage.list_keys()) - registered)
        permission_version_leakage = (
            active_version_leakage + cross_project_leakage + cross_workspace_leakage
        )
        critical_findings = permission_version_leakage + profile_leakage
        claims = response.get("claims") or []
        citations = response.get("citations") or []
        end_to_end_ms = (time.perf_counter() - case_started) * 1000
        return {
            "id": case["id"],
            "predicted_answerable": bool(response.get("answerable")),
            "retrieved_source_ids": retrieved,
            "cited_source_ids": cited,
            "answer": str(response.get("answer") or ""),
            "claim_count": len(claims),
            "supported_claim_count": _supported_claim_count(claims, citations),
            "permission_version_leakage": permission_version_leakage,
            "active_version_leakage": active_version_leakage,
            "profile_leakage": profile_leakage,
            "cross_project_leakage": cross_project_leakage,
            "cross_workspace_leakage": cross_workspace_leakage,
            "critical_high_security_findings": critical_findings,
            "retry_count": retry_count,
            "duplicate_count": duplicate_count,
            "orphan_count": orphan_count,
            "ingestion_ms": ingestion_ms,
            "retrieval_ms": retrieval_ms,
            "end_to_end_ms": end_to_end_ms,
            "queue_wait_ms": 0.0,
            "stage_duration_ms": {
                "ingestion": ingestion_ms,
                "retrieval": retrieval_ms,
                "answer": answer_ms,
            },
            "error_code": None,
        }

    def finalize(self) -> None:
        if not self._prepared or self._finalized:
            raise LocalProductionExecutorError(
                "production runtime finalize lifecycle is invalid"
            )
        error: Exception | None = None
        try:
            StorageObject = self._modules["StorageObject"]
            rows = self.db.query(StorageObject.storage_key, StorageObject.status).all()
            registered = _expected_storage_keys(rows)
            actual = set(self.storage.list_keys())
            if actual != registered:
                raise LocalProductionExecutorError(
                    "isolated MinIO objects do not match the storage registry"
                )
            if any(not self.storage.is_encrypted(key) for key in sorted(actual)):
                raise LocalProductionExecutorError(
                    "isolated MinIO contains a non-encrypted benchmark object"
                )
            if self.attestation() != self._build_attestation():
                raise LocalProductionExecutorError(
                    "production runtime provenance changed during execution"
                )
        except Exception as exc:  # noqa: BLE001 - final integrity boundary
            error = exc
        finally:
            self._finalized = True
            self._close_resources()
        if error is not None:
            if isinstance(error, LocalProductionExecutorError):
                raise error
            raise LocalProductionExecutorError(
                "production runtime final verification failed"
            ) from error

    def _close_resources(self) -> None:
        for resource, method in (
            (self.db, "close"),
            (self.engine, "dispose"),
            (self.redis, "close"),
        ):
            closer = getattr(resource, method, None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - best-effort after primary failure
                    pass


class ProductionCoreExecutor:
    """Runner-facing lifecycle wrapper around one isolated production runtime."""

    def __init__(
        self,
        *,
        runtime_factory: Callable[
            [Mapping[str, Any], Any, Any], Any
        ] = _ProductionRuntime,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._runtime: Any = None
        self._embedding_provider: Any = None
        self._generation_client: Any = None
        self._finalized = False

    @staticmethod
    def provenance() -> dict[str, Any]:
        return dict(PROVENANCE)

    def prepare(
        self, config: Mapping[str, Any], embedding: Any, generation: Any
    ) -> None:
        if self._runtime is not None or self._finalized:
            raise LocalProductionExecutorError("executor prepare lifecycle is invalid")
        runtime = self._runtime_factory(config, embedding, generation)
        runtime.prepare()
        self._runtime = runtime
        self._embedding_provider = embedding
        self._generation_client = generation

    def __call__(
        self,
        case: Mapping[str, Any],
        *,
        embedding_provider: Any,
        generation_client: Any,
        observe_request: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        del observe_request  # Providers are already bound to the runner observer.
        if (
            self._runtime is None
            or self._finalized
            or embedding_provider is not self._embedding_provider
            or generation_client is not self._generation_client
        ):
            raise LocalProductionExecutorError(
                "executor/provider lifecycle identity is invalid"
            )
        return self._runtime.execute_case(case)

    def attestation(self) -> dict[str, str]:
        if self._runtime is None:
            raise LocalProductionExecutorError("executor attestation is unavailable")
        return self._runtime.attestation()

    def finalize(self) -> None:
        if self._runtime is None or self._finalized:
            raise LocalProductionExecutorError("executor finalize lifecycle is invalid")
        self._finalized = True
        self._runtime.finalize()


production_case_executor = ProductionCoreExecutor()
