from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.application.context_vault_registry import (
    ContextVaultRegistryService,
    PersistencePolicyError,
    embedding_config_hash,
    model_config_hash,
    provider_config_hash,
)
from src.context_vault.adapters import (
    ClaudeCliAdapter,
    CodexCliAdapter,
    CoreContextEnvelope,
    WorkAuthority,
)
from src.context_vault.context_compiler import (
    ContextCompiler,
    ContextItem,
    ContextRole,
    DataClassification as ContextClassification,
    LoadTier,
    ProviderContextPolicy,
    verified_token_cost,
)
from src.context_vault.knowledge import (
    Actor,
    ActorKind,
    KnowledgePolicyError,
)
from src.context_vault.project_manifest import discover_project
from src.context_vault.registry import (
    Benchmark,
    Capability,
    DataClassification,
    DistanceMetric,
    EmbeddingProfile,
    HealthState,
    ModelRecord,
    ProviderRecord,
    SkillAdmissionPolicy,
    SkillRecord,
    SkillScope,
    TrustLevel,
)
from src.models import (
    CompiledContext,
    ContextManifest,
    ContextSource,
    KnowledgeConflict,
    KnowledgeItem,
    KnowledgeRevision,
    Principal,
    Project,
    SkillRegistry,
    WorkAttempt,
    WorkClaim,
    WorkItem,
    WorkReceipt,
    Workspace,
    WorkspaceMembership,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def _session() -> tuple[Session, tuple[Any, Any], Any]:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    return db, (engine, connection), transaction


def _close(db: Session, resources: tuple[Any, Any], transaction: Any) -> None:
    engine, connection = resources
    db.close()
    transaction.rollback()
    connection.close()
    engine.dispose()


def _scope(db: Session, *, project_id: uuid.UUID | None = None):
    owner = Principal(subject=f"owner:{uuid.uuid4()}")
    workspace = Workspace(name=f"workspace:{uuid.uuid4()}")
    db.add_all([owner, workspace])
    db.flush()
    project = Project(
        id=project_id or uuid.uuid4(),
        workspace_id=workspace.id,
        name=f"project:{uuid.uuid4()}",
    )
    db.add(project)
    db.flush()
    return owner, workspace, project


@pytest.mark.integration
def test_project_manifest_is_versioned_and_bound_to_exact_existing_project() -> None:
    db, resources, transaction = _session()
    try:
        recognized = discover_project(REPOSITORY_ROOT)
        owner, workspace, project = _scope(
            db, project_id=recognized.manifest.project_id
        )
        owner.subject = recognized.manifest.approval_policy.owner
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                principal_id=owner.id,
                role="admin",
            )
        )
        db.commit()
        unauthenticated_service = ContextVaultRegistryService(db)

        with pytest.raises(PersistencePolicyError, match="authenticated principal"):
            unauthenticated_service.register_project(recognized)
        service = ContextVaultRegistryService(db, authenticated_principal_id=owner.id)

        first = service.register_project(recognized)
        second = service.register_project(recognized)

        assert first["manifest_hash"] == recognized.manifest.manifest_hash
        assert first["workspace_id"] == str(workspace.id)
        assert second["replayed"] is True
        rows = db.query(ContextSource).filter_by(project_id=project.id).all()
        assert len(rows) == 1
        assert rows[0].metadata_json["kind"] == "project_manifest_projection"
        assert rows[0].metadata_json["repository_root"] == str(REPOSITORY_ROOT)
        drifted = replace(
            recognized,
            manifest=replace(
                recognized.manifest,
                repository=replace(
                    recognized.manifest.repository,
                    remote="https://example.invalid/other.git",
                ),
            ),
        )
        with pytest.raises(PersistencePolicyError, match="identity drifted"):
            service.register_project(drifted)
    finally:
        _close(db, resources, transaction)


@pytest.mark.integration
def test_knowledge_authority_is_versioned_locked_and_content_safe() -> None:
    db, resources, transaction = _session()
    try:
        owner, workspace, project = _scope(db)
        service = ContextVaultRegistryService(db)
        proposer = Actor(ActorKind.MODEL, "local-qwen")
        reviewer = Actor(ActorKind.HUMAN, "reviewer")
        approver = Actor(ActorKind.HUMAN, "approver")
        reviewer_principal = Principal(subject="reviewer")
        approver_principal = Principal(subject="approver")
        knowledge_source = ContextSource(
            project_id=project.id,
            source_id="approved-adr",
            version=1,
            content_hash="b" * 64,
            load_tier="RETRIEVE_ON_DEMAND",
            classification="INTERNAL",
            status="ACTIVE",
            provider_policy={},
            token_cost=1,
            metadata_json={},
        )
        db.add_all(
            [
                reviewer_principal,
                approver_principal,
                knowledge_source,
                ContextSource(
                    project_id=project.id,
                    source_id=f"project-manifest:{project.id}",
                    version=1,
                    content_hash="a" * 64,
                    load_tier="MUST_LOAD",
                    classification="INTERNAL",
                    status="ACTIVE",
                    provider_policy={},
                    token_cost=0,
                    metadata_json={
                        "approval_policy": {
                            "owner": owner.subject,
                            "approvers": ["reviewer", "approver"],
                            "human_required_for": [],
                        }
                    },
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    principal_id=reviewer_principal.id,
                    role="member",
                ),
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    principal_id=approver_principal.id,
                    role="member",
                ),
            ]
        )
        evidence_item = WorkItem(
            workspace_id=workspace.id,
            project_id=project.id,
            title="knowledge evidence",
            objective="verify the knowledge source",
            scope_json={"paths": ["adr.md"], "capabilities": ["read"]},
            expected_revision="d" * 40,
            status="DRAFT",
            created_by_principal_id=owner.id,
            acceptance_criteria=[{"id": "source", "required": True}],
            evidence_requirements=["verified source"],
        )
        db.add(evidence_item)
        db.flush()
        evidence_attempt = WorkAttempt(
            work_item_id=evidence_item.id,
            attempt_no=1,
            executor_id="verifier",
            status="COMPLETED",
            expected_revision="d" * 40,
            drift_token="c" * 64,
            idempotency_key="knowledge-evidence",
            claim_request_hash="b" * 64,
        )
        db.add(evidence_attempt)
        db.flush()
        apply_receipt = WorkReceipt(
            work_item_id=evidence_item.id,
            attempt_id=evidence_attempt.id,
            receipt_type="apply",
            idempotency_key="knowledge-source-read",
            request_hash="a" * 64,
            command="read source",
            tool="reader",
            action="read",
            started_at=NOW,
            ended_at=NOW,
            exit_status=0,
            output_artifact_hash=knowledge_source.content_hash,
            before_revision="d" * 40,
            after_revision="d" * 40,
            test_evidence_refs=[],
            acceptance_evidence=[],
            signer_type="service",
            signer_id="verifier",
            attestation={},
            is_terminal=False,
        )
        db.add(apply_receipt)
        db.flush()
        verify_receipt = WorkReceipt(
            work_item_id=evidence_item.id,
            attempt_id=evidence_attempt.id,
            parent_receipt_id=apply_receipt.id,
            receipt_type="verify",
            idempotency_key="knowledge-source-verify",
            request_hash="9" * 64,
            command="verify source",
            tool="verifier",
            action="verify",
            started_at=NOW,
            ended_at=NOW,
            exit_status=0,
            output_artifact_hash=knowledge_source.content_hash,
            before_revision="d" * 40,
            after_revision="d" * 40,
            test_evidence_refs=["source:hash"],
            acceptance_evidence=["source:verified"],
            signer_type="service",
            signer_id="verifier",
            attestation={},
            is_terminal=False,
        )
        db.add(verify_receipt)
        db.commit()
        source_refs = (f"context:{knowledge_source.id}",)
        evidence_refs = (f"receipt://work/{verify_receipt.id}",)
        review_service = ContextVaultRegistryService(
            db, authenticated_principal_id=reviewer_principal.id
        )
        approval_service = ContextVaultRegistryService(
            db, authenticated_principal_id=approver_principal.id
        )

        with pytest.raises(PersistencePolicyError, match="registered context"):
            service.propose_knowledge(
                workspace_id=workspace.id,
                project_id=project.id,
                stable_key="forged.references",
                content="Unproven claim.",
                source_ids=("source:invented",),
                evidence_ids=("receipt://work/" + str(uuid.uuid4()),),
                owner_id=owner.id,
                scope="project",
                confidence=0.1,
                classification="INTERNAL",
                proposed_by=proposer,
                valid_from=NOW,
                now=NOW,
            )

        first = service.propose_knowledge(
            workspace_id=workspace.id,
            project_id=project.id,
            stable_key="project.authority",
            content="PostgreSQL is authoritative.",
            source_ids=source_refs,
            evidence_ids=evidence_refs,
            owner_id=owner.id,
            scope="project",
            confidence=0.99,
            classification="INTERNAL",
            proposed_by=proposer,
            valid_from=NOW,
            now=NOW,
        )
        assert "PostgreSQL is authoritative." not in repr(first)
        with pytest.raises(KnowledgePolicyError, match="authenticated principal"):
            service.review_knowledge(uuid.UUID(first["revision_id"]), reviewer=reviewer)
        review_service.review_knowledge(
            uuid.UUID(first["revision_id"]), reviewer=reviewer
        )
        with pytest.raises(KnowledgePolicyError, match="model"):
            approval_service.approve_knowledge(
                uuid.UUID(first["revision_id"]),
                approver=proposer,
                now=NOW,
            )
        approved = approval_service.approve_knowledge(
            uuid.UUID(first["revision_id"]),
            approver=approver,
            now=NOW,
        )

        conflict = service.propose_knowledge(
            workspace_id=workspace.id,
            project_id=project.id,
            stable_key="project.authority",
            content="A transcript is authoritative.",
            source_ids=source_refs,
            evidence_ids=evidence_refs,
            owner_id=owner.id,
            scope="project",
            confidence=0.2,
            classification="INTERNAL",
            proposed_by=proposer,
            valid_from=NOW,
            now=NOW,
        )
        assert conflict["conflict_id"] is not None

        replacement = service.propose_knowledge(
            workspace_id=workspace.id,
            project_id=project.id,
            stable_key="project.authority",
            content="PostgreSQL Work Graph is authoritative.",
            source_ids=source_refs,
            evidence_ids=evidence_refs,
            owner_id=owner.id,
            scope="project",
            confidence=1.0,
            classification="INTERNAL",
            proposed_by=proposer,
            valid_from=NOW,
            supersedes_revision_id=uuid.UUID(first["revision_id"]),
            now=NOW,
        )
        review_service.review_knowledge(
            uuid.UUID(replacement["revision_id"]), reviewer=reviewer
        )
        approval_service.approve_knowledge(
            uuid.UUID(replacement["revision_id"]),
            approver=approver,
            now=NOW,
        )

        knowledge_item = db.get(KnowledgeItem, uuid.UUID(first["item_id"]))
        assert knowledge_item.active_revision_id == uuid.UUID(
            replacement["revision_id"]
        )
        old = db.get(KnowledgeRevision, uuid.UUID(approved["revision_id"]))
        assert old.status == "SUPERSEDED"
        assert old.terminal_by_id == "approver"
        assert db.query(KnowledgeConflict).filter_by(status="OPEN").count() == 1
    finally:
        _close(db, resources, transaction)


def _proposed_revision(
    *, item_id: uuid.UUID, owner_id: uuid.UUID, version: int = 1
) -> KnowledgeRevision:
    return KnowledgeRevision(
        knowledge_item_id=item_id,
        version=version,
        status="PROPOSED",
        content=f"knowledge revision {version}",
        content_hash=f"{version:064x}",
        source_refs=[f"source:{version}"],
        evidence_refs=[f"receipt:{version}"],
        owner_id=owner_id,
        scope="project",
        classification="INTERNAL",
        confidence=1.0,
        valid_from=NOW,
        proposed_by_type="model",
        proposed_by_id="local-model",
    )


def _validate_deferred_constraints(db: Session) -> None:
    db.flush()
    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    db.execute(text("SET CONSTRAINTS ALL DEFERRED"))


@pytest.mark.integration
def test_database_enforces_knowledge_lifecycle_and_exact_active_head() -> None:
    db, resources, transaction = _session()
    try:
        owner, workspace, project = _scope(db)
        item = KnowledgeItem(
            workspace_id=workspace.id,
            project_id=project.id,
            stable_key="database.lifecycle",
            scope="project",
            classification="INTERNAL",
            owner_id=owner.id,
        )
        db.add(item)
        db.commit()

        forged = _proposed_revision(item_id=item.id, owner_id=owner.id)
        forged.status = "APPROVED"
        forged.reviewed_by_type = "human"
        forged.reviewed_by_id = "reviewer"
        forged.approved_by_type = "human"
        forged.approved_by_id = "approver"
        db.add(forged)
        with pytest.raises(DBAPIError, match="must start at PROPOSED"):
            db.flush()
        db.rollback()

        item = db.query(KnowledgeItem).filter_by(stable_key="database.lifecycle").one()
        revision = _proposed_revision(item_id=item.id, owner_id=owner.id)
        db.add(revision)
        db.commit()

        revision.status = "APPROVED"
        revision.reviewed_by_type = "human"
        revision.reviewed_by_id = "reviewer"
        revision.approved_by_type = "human"
        revision.approved_by_id = "approver"
        with pytest.raises(DBAPIError, match="invalid knowledge transition"):
            db.flush()
        db.rollback()

        revision = db.get(KnowledgeRevision, revision.id)
        revision.status = "REVIEWED"
        revision.reviewed_by_type = "human"
        revision.reviewed_by_id = "reviewer"
        db.commit()

        revision.status = "APPROVED"
        revision.approved_by_type = "human"
        revision.approved_by_id = "approver"
        db.flush([revision])
        item.active_revision_id = revision.id
        _validate_deferred_constraints(db)
        db.commit()
        assert item.active_revision_id == revision.id

        revision.content = "silently rewritten"
        with pytest.raises(DBAPIError, match="provenance and content are immutable"):
            db.flush()
        db.rollback()

        item = db.get(KnowledgeItem, item.id)
        revision = db.get(KnowledgeRevision, revision.id)
        other_item = KnowledgeItem(
            workspace_id=workspace.id,
            project_id=project.id,
            stable_key="database.lifecycle.other",
            scope="project",
            classification="INTERNAL",
            owner_id=owner.id,
        )
        db.add(other_item)
        db.commit()
        other_item.active_revision_id = revision.id
        with pytest.raises(
            DBAPIError,
            match="active knowledge revision must belong to its item and be APPROVED",
        ):
            _validate_deferred_constraints(db)
        db.rollback()
    finally:
        _close(db, resources, transaction)


@pytest.mark.integration
def test_database_rejects_two_approved_heads_but_allows_supersession() -> None:
    db, resources, transaction = _session()
    try:
        owner, workspace, project = _scope(db)
        item = KnowledgeItem(
            workspace_id=workspace.id,
            project_id=project.id,
            stable_key="database.supersession",
            scope="project",
            classification="INTERNAL",
            owner_id=owner.id,
        )
        db.add(item)
        db.flush()
        first = _proposed_revision(item_id=item.id, owner_id=owner.id)
        db.add(first)
        db.commit()
        first.status = "REVIEWED"
        first.reviewed_by_type = "human"
        first.reviewed_by_id = "reviewer"
        db.commit()
        first.status = "APPROVED"
        first.approved_by_type = "human"
        first.approved_by_id = "approver"
        item.active_revision_id = first.id
        _validate_deferred_constraints(db)
        db.commit()

        second = _proposed_revision(item_id=item.id, owner_id=owner.id, version=2)
        second.supersedes_revision_id = first.id
        db.add(second)
        db.commit()
        second.status = "REVIEWED"
        second.reviewed_by_type = "human"
        second.reviewed_by_id = "reviewer-2"
        db.commit()
        second.status = "APPROVED"
        second.approved_by_type = "human"
        second.approved_by_id = "approver-2"
        item.active_revision_id = second.id
        with pytest.raises(
            DBAPIError,
            match="every APPROVED knowledge revision must be the active head",
        ):
            _validate_deferred_constraints(db)
        db.rollback()

        item = db.get(KnowledgeItem, item.id)
        first = db.get(KnowledgeRevision, first.id)
        second = db.get(KnowledgeRevision, second.id)
        first.status = "SUPERSEDED"
        first.terminal_by_type = "human"
        first.terminal_by_id = "approver-2"
        first.terminal_at = NOW
        db.flush([first])
        second.status = "APPROVED"
        second.approved_by_type = "human"
        second.approved_by_id = "approver-2"
        db.flush([second])
        item.active_revision_id = second.id
        _validate_deferred_constraints(db)
        db.commit()
        assert item.active_revision_id == second.id
        assert first.status == "SUPERSEDED"
        assert second.status == "APPROVED"
    finally:
        _close(db, resources, transaction)


def _provider() -> ProviderRecord:
    initial = ProviderRecord(
        provider_id="local-bge-m3",
        adapter_id="openai-compatible",
        locality="local",
        network_required=False,
        health=HealthState.HEALTHY,
        circuit_open=False,
        version="BAAI/bge-m3@6c6f",
        config_hash="a" * 64,
        metadata={"endpoint_ref": "config://providers/local-bge"},
    )
    return replace(initial, config_hash=provider_config_hash(initial))


def _model() -> ModelRecord:
    initial_profile = EmbeddingProfile(
        profile_id="bge-m3-v1",
        dimension=1024,
        distance=DistanceMetric.COSINE,
        query_prefix="query: ",
        document_prefix="passage: ",
        config_hash="b" * 64,
    )
    profile = replace(
        initial_profile,
        config_hash=embedding_config_hash(initial_profile),
    )
    initial = ModelRecord(
        model_id="BAAI/bge-m3@6c6f",
        provider_id="local-bge-m3",
        capabilities=frozenset({Capability.EMBEDDING}),
        context_limit=8192,
        output_limit=1024,
        allowed_data=frozenset(DataClassification),
        benchmark=Benchmark(10, 0, 0.8, 25),
        version="6c6f",
        config_hash="c" * 64,
        embedding_profile=profile,
    )
    return replace(initial, config_hash=model_config_hash(initial))


def _chat_model() -> ModelRecord:
    initial = ModelRecord(
        model_id="local-context-compiler@6c6f",
        provider_id="local-bge-m3",
        capabilities=frozenset({Capability.CHAT}),
        context_limit=8192,
        output_limit=1024,
        allowed_data=frozenset(DataClassification),
        benchmark=Benchmark(10, 0, 0.8, 25),
        version="6c6f",
        config_hash="0" * 64,
    )
    return replace(initial, config_hash=model_config_hash(initial))


@pytest.mark.integration
def test_registry_and_compiled_adapter_hashes_are_durably_attempt_bound() -> None:
    db, resources, transaction = _session()
    try:
        owner, workspace, project = _scope(db)
        service = ContextVaultRegistryService(db)
        provider = _provider()
        bge_model = _model()
        model = _chat_model()
        provider_result = service.register_provider(provider)
        assert provider_result["metadata_only"] is True
        service.register_model(bge_model)
        service.register_model(model)

        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                principal_id=owner.id,
                role="admin",
            )
        )
        db.add(
            ContextSource(
                project_id=project.id,
                source_id=f"project-manifest:{project.id}",
                version=1,
                content_hash="9" * 64,
                load_tier="MUST_LOAD",
                classification="INTERNAL",
                status="ACTIVE",
                provider_policy={
                    "local_allowed_classifications": [
                        "CONFIDENTIAL",
                        "INTERNAL",
                        "PUBLIC",
                        "RESTRICTED",
                    ],
                    "remote_allowed_classifications": ["INTERNAL", "PUBLIC"],
                },
                token_cost=0,
                metadata_json={
                    "approval_policy": {
                        "owner": owner.subject,
                        "approvers": [owner.subject],
                        "human_required_for": [],
                    },
                    "kind": "project_manifest_projection",
                    "token_budget": {
                        "context_window": 1024,
                        "reserved_output": 128,
                        "safety_margin": 64,
                    },
                },
            )
        )
        evidence_item = WorkItem(
            workspace_id=workspace.id,
            project_id=project.id,
            title="skill admission evidence",
            objective="install and scan the exact skill package",
            scope_json={
                "paths": ["skills/context-reader.pkg"],
                "capabilities": ["write_receipt_marker"],
            },
            expected_revision="8" * 40,
            status="DRAFT",
            created_by_principal_id=owner.id,
            acceptance_criteria=[{"id": "scan", "required": True}],
            evidence_requirements=["security_scan"],
        )
        db.add(evidence_item)
        db.flush()
        evidence_attempt = WorkAttempt(
            work_item_id=evidence_item.id,
            attempt_no=1,
            executor_id="security-scanner",
            status="COMPLETED",
            expected_revision="8" * 40,
            drift_token="6" * 64,
            idempotency_key="skill-evidence",
            claim_request_hash="5" * 64,
        )
        db.add(evidence_attempt)
        db.flush()
        install_receipt_id = uuid.uuid4()
        scan_receipt_id = uuid.uuid4()
        skill = SkillRecord(
            skill_id="context-reader",
            source_uri="git+https://example.invalid/skills.git#" + "d" * 40,
            version="1.0.0",
            commit_sha="d" * 40,
            package_hash="e" * 64,
            publisher="context-team",
            owner=owner.subject,
            trust_level=TrustLevel.VERIFIED,
            required_tools=frozenset({"read_file"}),
            required_permissions=frozenset({"read_project"}),
            network_required=False,
            filesystem_scopes=("/workspace/project",),
            supported_adapters=frozenset({"codex"}),
            operation_receipt_refs=(f"receipt://work/{install_receipt_id}",),
            security_scan_receipt_ref=f"receipt://work/{scan_receipt_id}",
            license_id="Apache-2.0",
            enabled_scope=SkillScope.PROJECT,
            enabled_scope_id=str(project.id),
        )
        install_receipt = WorkReceipt(
            id=install_receipt_id,
            work_item_id=evidence_item.id,
            attempt_id=evidence_attempt.id,
            receipt_type="apply",
            idempotency_key="skill-install",
            request_hash="4" * 64,
            command="install exact package",
            tool="skill-installer",
            action="install",
            started_at=NOW,
            ended_at=NOW,
            exit_status=0,
            output_artifact_hash="e" * 64,
            before_revision="8" * 40,
            after_revision="2" * 40,
            test_evidence_refs=[],
            acceptance_evidence=[],
            signer_type="human",
            signer_id=owner.subject,
            attestation={
                "commit_sha": "d" * 40,
                "package_hash": "e" * 64,
                "skill_record_hash": skill.record_hash,
            },
            is_terminal=False,
        )
        db.add(install_receipt)
        db.flush()
        scan_receipt = WorkReceipt(
            id=scan_receipt_id,
            work_item_id=evidence_item.id,
            attempt_id=evidence_attempt.id,
            parent_receipt_id=install_receipt.id,
            receipt_type="verify",
            idempotency_key="skill-scan",
            request_hash="1" * 64,
            command="scan exact package",
            tool="security-scanner",
            action="verify",
            started_at=NOW,
            ended_at=NOW,
            exit_status=0,
            output_artifact_hash="e" * 64,
            before_revision="2" * 40,
            after_revision="2" * 40,
            test_evidence_refs=["scanner:clean"],
            acceptance_evidence=["receipt:scan"],
            signer_type="human",
            signer_id=owner.subject,
            attestation={
                "commit_sha": "d" * 40,
                "package_hash": "e" * 64,
                "skill_record_hash": skill.record_hash,
            },
            is_terminal=False,
        )
        db.add(scan_receipt)
        db.commit()

        registration_service = ContextVaultRegistryService(
            db, authenticated_principal_id=owner.id
        )
        with pytest.raises(PersistencePolicyError, match="authenticated principal"):
            service.register_skill(skill)
        registered = registration_service.register_skill(skill)
        assert (
            db.get(SkillRegistry, skill.skill_id).receipt_refs["record_hash"]
            == (registered["record_hash"])
        )
        admitted = service.verify_registered_skill(
            skill.skill_id,
            observed_package_hash=skill.package_hash,
            adapter_id="codex",
            policy=SkillAdmissionPolicy(
                allowed_trust_levels=frozenset({TrustLevel.VERIFIED}),
                allowed_tools=frozenset({"read_file"}),
                allowed_permissions=frozenset({"read_project"}),
                allow_network=False,
                filesystem_roots=("/workspace/project",),
            ),
        )
        assert admitted.registry_record_hash == skill.record_hash
        forged_skill = replace(
            skill,
            skill_id="context-reader-forged",
            package_hash="f" * 64,
        )
        with pytest.raises(PersistencePolicyError, match="exact registry record"):
            registration_service.register_skill(forged_skill)

        item = WorkItem(
            workspace_id=workspace.id,
            project_id=project.id,
            title="compile",
            objective="bind context",
            scope_json={},
            expected_revision="a" * 40,
            status="CLAIMED",
            revision=1,
            created_by_principal_id=owner.id,
            acceptance_criteria=[],
            evidence_requirements=[],
        )
        db.add(item)
        db.flush()
        attempt = WorkAttempt(
            work_item_id=item.id,
            attempt_no=1,
            executor_id="codex",
            adapter_id="codex",
            model_id=model.model_id,
            provider_id=provider.provider_id,
            status="CLAIMED",
            expected_revision="a" * 40,
            drift_token="f" * 64,
            idempotency_key="compile-1",
            claim_request_hash="0" * 64,
        )
        db.add(attempt)
        db.flush()
        claim = WorkClaim(
            work_item_id=item.id,
            attempt_id=attempt.id,
            resource_scope={
                "paths": ["evidence/marker.json"],
                "capabilities": ["write_receipt_marker"],
            },
            scope_hash="1" * 64,
            claimant_id="codex",
            acquired_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            heartbeat_at=datetime.now(timezone.utc),
            fencing_token=1,
            status="active",
        )
        db.add(claim)
        db.flush()
        item.active_attempt_id = attempt.id
        item.active_fencing_token = claim.fencing_token
        content = "Never disclose secrets."
        db.add(
            ContextSource(
                project_id=project.id,
                source_id="security",
                version=1,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                load_tier="MUST_LOAD",
                classification="INTERNAL",
                status="ACTIVE",
                provider_policy={},
                token_cost=verified_token_cost(content),
                metadata_json={},
            )
        )
        db.commit()
        work_content = json.dumps(
            {
                "acceptance_criteria": item.acceptance_criteria,
                "attempt_id": str(attempt.id),
                "claim_id": str(claim.id),
                "evidence_requirements": item.evidence_requirements,
                "expected_revision": item.expected_revision,
                "fencing_token": item.active_fencing_token,
                "objective": item.objective,
                "revision": item.revision,
                "scope": item.scope_json,
                "status": item.status,
                "work_item_id": str(item.id),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        compiled = ContextCompiler().compile(
            attempt_id=str(attempt.id),
            items=(
                ContextItem.from_content(
                    source_id=f"work-item:{item.id}",
                    logical_id="active-work",
                    version=item.revision,
                    content=work_content,
                    reason="current PostgreSQL work authority",
                    token_cost=verified_token_cost(work_content),
                    load_tier=LoadTier.MUST_LOAD,
                    classification=ContextClassification.INTERNAL,
                    role=ContextRole.ACTIVE_WORK,
                ),
                ContextItem.from_content(
                    source_id="security",
                    logical_id="security",
                    version=1,
                    content=content,
                    reason="mandatory policy",
                    token_cost=verified_token_cost(content),
                    load_tier=LoadTier.MUST_LOAD,
                    classification=ContextClassification.INTERNAL,
                    role=ContextRole.SECURITY_POLICY,
                ),
            ),
            provider_policy=ProviderContextPolicy(
                provider_id=provider.provider_id,
                is_remote=False,
                allowed_classifications=frozenset({ContextClassification.INTERNAL}),
            ),
            context_window=1024,
            reserved_output=128,
            safety_margin=64,
        )
        authority = WorkAuthority(
            work_item_id=str(item.id),
            attempt_id=str(attempt.id),
            claim_id=str(claim.id),
            fencing_token=1,
            current_revision="a" * 40,
        )
        adapter_projection = CodexCliAdapter().project(
            CoreContextEnvelope.from_compiled_context(compiled),
            authority,
            model_id=model.model_id,
            tool_mapping={"read": "read_file"},
        )
        forged_payload = compiled.canonical_payload()
        forged_payload["provider"]["allowed_classifications"] = ["PUBLIC"]
        forged_json = json.dumps(
            forged_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        forged_compiled = replace(
            compiled,
            manifest_hash=hashlib.sha256(forged_json.encode()).hexdigest(),
            _canonical_payload_json=forged_json,
        )
        forged_projection = CodexCliAdapter().project(
            CoreContextEnvelope.from_compiled_context(forged_compiled),
            authority,
            model_id=model.model_id,
            tool_mapping={"read": "read_file"},
        )
        with pytest.raises(PersistencePolicyError, match="outside provider policy"):
            service.bind_compiled_context(
                compiled=forged_compiled,
                projections=(forged_projection,),
                model_id=model.model_id,
                context_window=1024,
                reserved_output=128,
                safety_margin=64,
            )
        assert db.query(ContextManifest).filter_by(attempt_id=attempt.id).count() == 0

        omitted = ContextSource(
            project_id=project.id,
            source_id="mandatory-omitted",
            version=1,
            content_hash="7" * 64,
            load_tier="MUST_LOAD",
            classification="INTERNAL",
            status="ACTIVE",
            provider_policy={},
            token_cost=1,
            metadata_json={},
        )
        db.add(omitted)
        db.commit()
        with pytest.raises(PersistencePolicyError, match="omitted"):
            service.bind_compiled_context(
                compiled=compiled,
                projections=(adapter_projection,),
                model_id=model.model_id,
                context_window=1024,
                reserved_output=128,
                safety_margin=64,
            )
        assert db.query(ContextManifest).filter_by(attempt_id=attempt.id).count() == 0
        omitted.status = "STALE"
        db.commit()

        stale_authority = replace(authority, claim_id=str(uuid.uuid4()))
        stale_projection = ClaudeCliAdapter().project(
            CoreContextEnvelope.from_compiled_context(compiled),
            stale_authority,
            model_id=model.model_id,
            tool_mapping={"read": "read_file"},
        )
        with pytest.raises(PersistencePolicyError, match="authority binding is stale"):
            service.bind_compiled_context(
                compiled=compiled,
                projections=(adapter_projection, stale_projection),
                model_id=model.model_id,
                context_window=1024,
                reserved_output=128,
                safety_margin=64,
            )
        assert db.query(ContextManifest).filter_by(attempt_id=attempt.id).count() == 0
        assert db.query(CompiledContext).count() == 0

        result = service.bind_compiled_context(
            compiled=compiled,
            projections=(adapter_projection,),
            model_id=model.model_id,
            context_window=1024,
            reserved_output=128,
            safety_margin=64,
        )

        db.refresh(attempt)
        assert attempt.input_context_manifest_hash == compiled.manifest_hash
        assert result["adapter_hashes"]["codex"] == (
            adapter_projection.projection_receipt_hash
        )
        assert db.query(ContextManifest).filter_by(attempt_id=attempt.id).count() == 1
        assert db.query(CompiledContext).count() == 1
    finally:
        _close(db, resources, transaction)
