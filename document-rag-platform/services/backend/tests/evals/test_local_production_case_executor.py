"""Contracts for the concrete trusted-local production-core executor."""

from __future__ import annotations

import base64
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[5]
SCRIPT = REPO / "scripts/local_production_case_executor.py"


def _module():
    spec = importlib.util.spec_from_file_location("cv_local_case_executor", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Runtime:
    def __init__(self, config, embedding, generation):
        self.config = config
        self.embedding = embedding
        self.generation = generation
        self.events = []

    def prepare(self):
        self.events.append("prepare")

    def execute_case(self, case):
        self.events.append(("case", case["id"]))
        return {"id": case["id"]}

    def retrieve_case(self, case):
        self.events.append(("retrieve", case["id"]))
        return {"id": case["id"], "private": "handle"}

    def answer_case(self, handle):
        self.events.append(("answer", handle["id"]))
        return {"id": handle["id"]}

    def attestation(self):
        return {
            "embedding_profile_hash": "a" * 64,
            "prompt_hash": "b" * 64,
            "pipeline_config_hash": "c" * 64,
        }

    def finalize(self):
        self.events.append("finalize")


def test_executor_binds_exact_runtime_and_provider_instances():
    module = _module()
    created = []

    def factory(config, embedding, generation):
        runtime = _Runtime(config, embedding, generation)
        created.append(runtime)
        return runtime

    executor = module.ProductionCoreExecutor(runtime_factory=factory)
    embedding = object()
    generation = object()
    config = {"execution_sha256": "a" * 64}
    executor.prepare(config, embedding, generation)

    assert executor(
        {"id": "case-1"},
        embedding_provider=embedding,
        generation_client=generation,
        observe_request=lambda _event: None,
    ) == {"id": "case-1"}
    assert executor.attestation()["embedding_profile_hash"] == "a" * 64
    executor.finalize()
    assert created[0].events == ["prepare", ("case", "case-1"), "finalize"]


def test_executor_rejects_unprepared_wrong_provider_and_reuse():
    module = _module()
    executor = module.ProductionCoreExecutor(runtime_factory=_Runtime)
    embedding = object()
    generation = object()

    def call(e=embedding, g=generation):
        return executor(
            {"id": "case-1"},
            embedding_provider=e,
            generation_client=g,
            observe_request=lambda _event: None,
        )

    with pytest.raises(module.LocalProductionExecutorError, match="lifecycle"):
        call()
    executor.prepare({"execution_sha256": "a" * 64}, embedding, generation)
    with pytest.raises(module.LocalProductionExecutorError, match="identity"):
        call(e=object())
    executor.finalize()
    with pytest.raises(module.LocalProductionExecutorError, match="lifecycle"):
        call()
    with pytest.raises(module.LocalProductionExecutorError, match="prepare"):
        executor.prepare({}, embedding, generation)
    with pytest.raises(module.LocalProductionExecutorError, match="finalize"):
        executor.finalize()


def test_provenance_is_exact_and_does_not_claim_celery_delivery():
    module = _module()
    assert module.ProductionCoreExecutor.provenance() == {
        "pipeline": "context-vault-production-core",
        "ingestion": "IngestionOrchestrator.accept_source/process_job",
        "retrieval": "RetrievalService.retrieve",
        "answer": "application.answer_service.generate_answer",
        "celery_delivery_exercised": False,
        "redis_role": "health-only",
        "model_residency_strategy": "phased",
    }


def test_executor_exposes_two_phase_retrieval_then_answer_contract():
    module = _module()
    created = []

    def factory(config, embedding, generation):
        runtime = _Runtime(config, embedding, generation)
        created.append(runtime)
        return runtime

    executor = module.ProductionCoreExecutor(runtime_factory=factory)
    embedding = object()
    generation = object()
    executor.prepare({"execution_sha256": "a" * 64}, embedding, generation)

    first = executor.phase_one(
        {"id": "one"},
        embedding_provider=embedding,
        observe_request=lambda _event: None,
    )
    second = executor.phase_one(
        {"id": "two"},
        embedding_provider=embedding,
        observe_request=lambda _event: None,
    )
    assert created[0].events == [
        "prepare",
        ("retrieve", "one"),
        ("retrieve", "two"),
    ]

    assert executor.phase_two(
        {"id": "one"},
        handle=first,
        generation_client=generation,
        observe_request=lambda _event: None,
    ) == {"id": "one"}
    assert executor.phase_two(
        {"id": "two"},
        handle=second,
        generation_client=generation,
        observe_request=lambda _event: None,
    ) == {"id": "two"}
    assert created[0].events[-2:] == [("answer", "one"), ("answer", "two")]
    executor.finalize()


def test_runtime_rejects_foreign_and_replayed_retrieval_handles():
    module = _module()
    runtime = module._ProductionRuntime.__new__(module._ProductionRuntime)
    runtime._prepared = True
    runtime._finalized = False
    case = {"id": "one", "query": "question"}
    handle = {
        "case": case,
        "case_started": 0.0,
        "principal": SimpleNamespace(id="principal"),
        "workspace": SimpleNamespace(id="workspace"),
        "project": SimpleNamespace(id="project"),
        "logical_by_source_version": {("document", "version"): "logical-source"},
        "active_source_versions": {
            "document": ("version", "logical-source"),
            "prior-document": ("prior-version", "prior-source"),
        },
        "job_ids": [],
        "ingestion_ms": 1.0,
        "retrieval": SimpleNamespace(
            ranked_candidates=[
                SimpleNamespace(
                    chunk_id="chunk",
                    document_id="document",
                    version_id="version",
                    embedding_profile_id="profile",
                    project_id="project",
                    workspace_id="workspace",
                ),
                SimpleNamespace(
                    chunk_id="prior-chunk",
                    document_id="prior-document",
                    version_id="prior-version",
                    embedding_profile_id="profile",
                    project_id="project",
                    workspace_id="workspace",
                ),
            ]
        ),
        "chunk_resolver": lambda _chunk_id: None,
        "retrieval_ms": 1.0,
    }
    runtime._pending_handles = {"one": handle}

    foreign = dict(handle)
    with pytest.raises(module.LocalProductionExecutorError, match="invalid"):
        runtime.answer_case(foreign)

    class Column:
        @staticmethod
        def in_(_values):
            return True

    class IngestionAttempt:
        job_id = Column()

    class StorageObject:
        storage_key = object()

    class Query:
        def filter(self, *_args):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def all():
            return []

    class Database:
        @staticmethod
        def commit():
            return None

        @staticmethod
        def query(*_args):
            return Query()

    runtime.db = Database()
    runtime.storage = SimpleNamespace(list_keys=lambda: [])
    runtime.generation_client = object()
    runtime.profile = SimpleNamespace(id="profile")
    runtime._modules = {
        "IngestionAttempt": IngestionAttempt,
        "StorageObject": StorageObject,
        "ensure_conversation": lambda *_args, **_kwargs: "conversation",
        "generate_answer": lambda **_kwargs: {
            "answerable": False,
            "answer": "No context",
            "claims": [],
            "citations": [
                {
                    "label": "S1",
                    "document_id": "document",
                    "version_id": "version",
                    "snippet": "",
                }
            ],
        },
    }

    answer = runtime.answer_case(handle)
    assert answer["id"] == "one"
    assert answer["retrieved_source_ids"][0] == "logical-source"
    assert answer["retrieved_source_ids"][1].startswith("out-of-case-source-")
    assert answer["cited_source_ids"] == ["logical-source"]
    assert answer["active_version_leakage"] == 0
    with pytest.raises(module.LocalProductionExecutorError, match="already consumed"):
        runtime.answer_case(handle)


@pytest.mark.parametrize(
    ("document_scope", "expected_allowed"),
    [("case", "single-document"), ("project", None)],
)
def test_runtime_ingests_revisions_and_applies_document_scope(
    document_scope, expected_allowed
):
    module = _module()
    actual_document_id = uuid.uuid4()
    first_version_id = uuid.uuid4()
    second_version_id = uuid.uuid4()
    document_record = SimpleNamespace(id=actual_document_id)
    commands = []
    retrieval_scopes = []

    class Record:
        def __init__(self, **values):
            vars(self).update(values)

    class Database:
        @staticmethod
        def get(_model, identity):
            assert identity == actual_document_id
            return document_record

    class Orchestrator:
        def __init__(self, _db, _storage):
            pass

        def accept_source(self, command):
            commands.append(command)
            version_id = first_version_id if len(commands) == 1 else second_version_id
            return SimpleNamespace(
                document_id=actual_document_id,
                version_id=version_id,
                job_id=uuid.uuid4(),
                status="queued",
                quarantine_reason=None,
            )

        @staticmethod
        def process_job(*_args, **_kwargs):
            return {"status": "completed"}

    class RetrievalScope(Record):
        def __init__(self, **values):
            super().__init__(**values)
            retrieval_scopes.append(self)

    class Retriever:
        def __init__(self, **_kwargs):
            pass

    class RetrievalService:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def retrieve(*_args, **_kwargs):
            return SimpleNamespace(ranked_candidates=[])

    runtime = module._ProductionRuntime.__new__(module._ProductionRuntime)
    runtime._prepared = True
    runtime._finalized = False
    runtime._pending_handles = {}
    runtime.config = {"execution_sha256": "a" * 64}
    runtime.db = Database()
    runtime.storage = object()
    runtime.embedding_provider = SimpleNamespace(
        embed=lambda _texts: [], embed_one=lambda _text: []
    )
    runtime.profile = SimpleNamespace(id=uuid.uuid4())
    runtime._identity = lambda _case: (
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(id=uuid.uuid4()),
        object(),
    )
    runtime._modules = {
        "require_project_access": lambda *_args: None,
        "IngestionOrchestrator": Orchestrator,
        "AcceptSourceCommand": Record,
        "SourceDescriptor": Record,
        "Document": object(),
        "RetrievalScope": RetrievalScope,
        "_build_resolvers": lambda *_args: (lambda _id: None, lambda _id: []),
        "RetrievalService": RetrievalService,
        "DenseVectorRetriever": Retriever,
        "LexicalRetriever": Retriever,
        "IdentifierRetriever": Retriever,
    }
    case = {
        "id": "revision-case",
        "query": "What is current?",
        "scope": "documents",
        "document_scope": document_scope,
        "documents": [
            {
                "document_id": "policy-v1",
                "filename": "policy-v1.txt",
                "mime_type": "text/plain",
                "source_type": "document",
                "content_base64": base64.b64encode(b"old").decode(),
                "classification": "internal",
                "revises_document_id": None,
            },
            {
                "document_id": "policy-v2",
                "filename": "policy-v2.txt",
                "mime_type": "text/plain",
                "source_type": "document",
                "content_base64": base64.b64encode(b"current").decode(),
                "classification": "internal",
                "revises_document_id": "policy-v1",
            },
        ],
    }

    handle = runtime.retrieve_case(case)

    assert commands[0].existing_document is None
    assert commands[1].existing_document is document_record
    assert retrieval_scopes[0].allowed_document_ids == (
        (actual_document_id,) if expected_allowed else None
    )
    assert handle["logical_by_source_version"] == {
        (str(actual_document_id), str(first_version_id)): "policy-v1",
        (str(actual_document_id), str(second_version_id)): "policy-v2",
    }
    assert handle["active_source_versions"] == {
        str(actual_document_id): (str(second_version_id), "policy-v2")
    }


def test_version_aware_mapping_preserves_logical_revision_identity():
    module = _module()
    mappings = module._source_version_mappings(
        {"policy-v1": "document", "policy-v2": "document"},
        {"policy-v1": "version-1", "policy-v2": "version-2"},
    )
    assert mappings == (
        {
            ("document", "version-1"): "policy-v1",
            ("document", "version-2"): "policy-v2",
        },
        {"document": ("version-2", "policy-v2")},
    )


def test_foreign_response_citation_is_not_silently_dropped():
    module = _module()
    runtime = module._ProductionRuntime.__new__(module._ProductionRuntime)
    runtime._prepared = True
    runtime._finalized = False
    case = {"id": "foreign-citation", "query": "question"}
    handle = {
        "case": case,
        "case_started": 0.0,
        "principal": SimpleNamespace(id="principal"),
        "workspace": SimpleNamespace(id="workspace"),
        "project": SimpleNamespace(id="project"),
        "logical_by_source_version": {
            ("document", "version"): "source",
            ("document", "old-version"): "old-source",
        },
        "active_source_versions": {"document": ("version", "source")},
        "job_ids": [],
        "ingestion_ms": 1.0,
        "retrieval": SimpleNamespace(ranked_candidates=[]),
        "chunk_resolver": lambda _chunk_id: None,
        "retrieval_ms": 1.0,
    }
    runtime._pending_handles = {case["id"]: handle}

    class Column:
        @staticmethod
        def in_(_values):
            return True

    class IngestionAttempt:
        job_id = Column()

    class StorageObject:
        storage_key = object()

    class Query:
        def filter(self, *_args):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def all():
            return []

    runtime.db = SimpleNamespace(commit=lambda: None, query=lambda *_args: Query())
    runtime.storage = SimpleNamespace(list_keys=lambda: [])
    runtime.generation_client = object()
    runtime.profile = SimpleNamespace(id="profile")
    runtime._modules = {
        "IngestionAttempt": IngestionAttempt,
        "StorageObject": StorageObject,
        "ensure_conversation": lambda *_args, **_kwargs: "conversation",
        "generate_answer": lambda **_kwargs: {
            "answerable": True,
            "answer": "unsafe",
            "claims": [],
            "citations": [
                {
                    "label": "S1",
                    "document_id": "document",
                    "version_id": "old-version",
                    "snippet": "unsafe",
                }
            ],
        },
    }

    result = runtime.answer_case(handle)

    assert len(result["cited_source_ids"]) == 1
    assert result["cited_source_ids"][0].startswith("invalid-citation-")
    assert result["critical_high_security_findings"] == 1


def test_runtime_finalize_rejects_unanswered_handles_and_closes_resources():
    module = _module()
    events = []

    class Resource:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append(f"{self.name}.close")

        def dispose(self):
            events.append(f"{self.name}.dispose")

    runtime = module._ProductionRuntime.__new__(module._ProductionRuntime)
    runtime._prepared = True
    runtime._finalized = False
    runtime._pending_handles = {"one": {"case": {"id": "one"}}}
    runtime.db = Resource("db")
    runtime.engine = Resource("engine")
    runtime.redis = Resource("redis")

    with pytest.raises(module.LocalProductionExecutorError, match="unanswered"):
        runtime.finalize()

    assert runtime._finalized is True
    assert events == ["db.close", "engine.dispose", "redis.close"]


@pytest.mark.parametrize(
    ("persona", "role"),
    [
        ("admin", "admin"),
        ("project-admin", "admin"),
        ("member", "member"),
        ("project-member", "member"),
        ("reader", "reader"),
        ("project-reader", "reader"),
    ],
)
def test_permission_persona_mapping_is_bounded(persona, role):
    module = _module()
    assert module._role(persona) == role


def test_unknown_permission_persona_fails_closed():
    module = _module()
    with pytest.raises(module.LocalProductionExecutorError, match="persona"):
        module._role("owner")


def test_stable_ids_are_dataset_and_fixture_bound():
    module = _module()
    first = module._stable_uuid("a" * 64, "workspace", "one")
    assert first == module._stable_uuid("a" * 64, "workspace", "one")
    assert first != module._stable_uuid("b" * 64, "workspace", "one")
    assert first != module._stable_uuid("a" * 64, "workspace", "two")


def test_candidate_value_reads_final_retrieval_provenance():
    module = _module()

    class Representative:
        document_id = "document"

    class Fused:
        representative = Representative()

    class Candidate:
        fused_hit = Fused()

    assert module._candidate_value(Candidate(), "document_id") == "document"
    assert module._candidate_value(Candidate(), "missing") is None


def test_exact_migration_seed_is_admitted_and_extra_identity_is_rejected():
    module = _module()
    principal = (
        module.LEGACY_PRINCIPAL_ID,
        "migration:legacy-owner",
        "Legacy data owner (inactive)",
        False,
    )
    workspace = (module.LEGACY_WORKSPACE_ID, "Legacy migrated workspace")
    membership = (
        module.LEGACY_WORKSPACE_ID,
        module.LEGACY_PRINCIPAL_ID,
        "admin",
    )
    module._assert_exact_migration_seed(
        principals=[principal],
        workspaces=[workspace],
        memberships=[membership],
    )
    with pytest.raises(module.LocalProductionExecutorError, match="principal seed"):
        module._assert_exact_migration_seed(
            principals=[principal, principal],
            workspaces=[workspace],
            memberships=[membership],
        )


def test_deleted_staging_registry_rows_are_not_expected_in_minio():
    module = _module()
    rows = [
        ("staging/removed", "deleted"),
        ("immutable/retained", "referenced"),
        ("quarantine/retained", "quarantined"),
    ]
    assert module._expected_storage_keys(rows) == {
        "immutable/retained",
        "quarantine/retained",
    }
    with pytest.raises(module.LocalProductionExecutorError, match="malformed"):
        module._expected_storage_keys([("broken", "unknown")])


def test_supported_claim_count_requires_literal_support_in_every_cited_snippet():
    module = _module()
    citations = [
        {"label": "S1", "snippet": "The approved retention period is 30 days."},
        {"label": "S2", "snippet": "An unrelated statement."},
    ]
    claims = [
        {"claim_text": "retention period is 30 days", "source_labels": ["S1"]},
        {
            "claim_text": "retention period is 30 days",
            "source_labels": ["S1", "S2"],
        },
        {"claim_text": "unsupported assertion", "source_labels": ["S1"]},
    ]
    assert module._supported_claim_count(claims, citations) == 1
