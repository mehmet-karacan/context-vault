"""Contracts for the concrete trusted-local production-core executor."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
    }


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
