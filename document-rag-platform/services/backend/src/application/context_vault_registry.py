"""PostgreSQL authority for Context Vault project, knowledge and registries.

The domain modules validate untrusted metadata and lifecycle policy.  This adapter
adds transaction boundaries, row locks and durable hash bindings without exposing
raw knowledge or compiled context in its return values.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.context_vault.adapters import (
    AdapterProjection,
    CoreContextEnvelope,
    assert_projection_conformance,
)
from src.context_vault.context_compiler import (
    CompiledContext as DomainCompiledContext,
    DataClassification as ContextDataClassification,
)
from src.context_vault.knowledge import (
    Actor,
    ActorKind,
    ApprovalPolicy,
    KnowledgePolicyError,
    KnowledgeRevision as DomainKnowledgeRevision,
    KnowledgeStatus,
)
from src.context_vault.project_manifest import RecognizedProject
from src.context_vault.registry import (
    Benchmark,
    Capability,
    DataClassification,
    DistanceMetric,
    EmbeddingProfile,
    HealthState,
    ModelRecord,
    ProviderModelRegistry,
    ProviderRecord,
    SkillAdmissionPolicy,
    SkillProjection,
    SkillRecord,
    SkillRegistry as DomainSkillRegistry,
    canonical_hash,
)
from src.models import (
    CompiledContext,
    ContextManifest,
    ContextSource,
    KnowledgeConflict,
    KnowledgeItem,
    KnowledgeRevision,
    ModelRegistry,
    Principal,
    Project,
    ProviderRegistry,
    SkillRegistry,
    WorkAttempt,
    WorkClaim,
    WorkItem,
    WorkReceipt,
    WorkspaceMembership,
)


class PersistencePolicyError(ValueError):
    """A durable operation failed an identity, integrity or policy check."""


def _plain(value: Any) -> Any:
    if isinstance(value, (Mapping, MappingProxyType)):
        return {str(key): _plain(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _provider_config_payload(record: ProviderRecord) -> dict[str, Any]:
    return {
        "adapter_id": record.adapter_id,
        "locality": record.locality,
        "metadata": _plain(record.metadata),
        "network_required": record.network_required,
        "provider_id": record.provider_id,
        "secret_ref": record.secret_ref,
        "version": record.version,
    }


def provider_config_hash(record: ProviderRecord) -> str:
    """Hash the immutable, secretless provider configuration."""

    return canonical_hash(_provider_config_payload(record))


def _embedding_payload(record: ModelRecord) -> dict[str, Any] | None:
    profile = record.embedding_profile
    if profile is None:
        return None
    return {
        "config_hash": profile.config_hash,
        "dimension": profile.dimension,
        "distance": profile.distance.value,
        "document_prefix": profile.document_prefix,
        "profile_id": profile.profile_id,
        "query_prefix": profile.query_prefix,
    }


def embedding_config_hash(profile: EmbeddingProfile) -> str:
    """Hash vector dimension, distance and exact query/document prefixes."""

    return canonical_hash(
        {
            "dimension": profile.dimension,
            "distance": profile.distance.value,
            "document_prefix": profile.document_prefix,
            "profile_id": profile.profile_id,
            "query_prefix": profile.query_prefix,
        }
    )


def _model_config_payload(record: ModelRecord) -> dict[str, Any]:
    return {
        "allowed_data": sorted(value.value for value in record.allowed_data),
        "benchmark": dataclasses.asdict(record.benchmark),
        "capabilities": sorted(value.value for value in record.capabilities),
        "context_limit": record.context_limit,
        "embedding_profile": _embedding_payload(record),
        "fallback_model_ids": list(record.fallback_model_ids),
        "model_id": record.model_id,
        "output_limit": record.output_limit,
        "provider_id": record.provider_id,
        "version": record.version,
    }


def model_config_hash(record: ModelRecord) -> str:
    """Hash model identity, capability, data policy and vector compatibility."""

    return canonical_hash(_model_config_payload(record))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _actor_db_kind(actor: Actor) -> str:
    return actor.kind.value.casefold()


def _actor(kind: str | None, actor_id: str | None) -> Actor | None:
    if kind is None and actor_id is None:
        return None
    if kind is None or actor_id is None:
        raise PersistencePolicyError("knowledge authority audit is incomplete")
    try:
        return Actor(ActorKind(kind.upper()), actor_id)
    except (ValueError, KnowledgePolicyError) as exc:
        raise PersistencePolicyError("knowledge authority audit is invalid") from exc


class ContextVaultRegistryService:
    """Transactional PostgreSQL composition for Context Vault domain services."""

    def __init__(
        self,
        db: Session,
        *,
        authenticated_principal_id: uuid.UUID | None = None,
        trusted_policy_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.db = db
        self.authenticated_principal_id = authenticated_principal_id
        self.trusted_policy_ids = trusted_policy_ids

    def register_project(self, recognized: RecognizedProject) -> Mapping[str, Any]:
        """Bind a validated manifest to its existing exact project and root."""

        principal_id = self.authenticated_principal_id
        if principal_id is None:
            raise PersistencePolicyError(
                "project registration requires an authenticated principal"
            )
        manifest = recognized.manifest
        root = recognized.root.resolve(strict=True)
        manifest_path = recognized.manifest_path.resolve(strict=True)
        if manifest_path.parent != root / ".contextvault":
            raise PersistencePolicyError("manifest does not belong to recognized root")
        project = self.db.execute(
            select(Project).where(Project.id == manifest.project_id).with_for_update()
        ).scalar_one_or_none()
        if project is None or project.deleted_at is not None:
            raise PersistencePolicyError("manifest project_id is not registered")
        principal = self.db.get(Principal, principal_id)
        membership = self.db.get(
            WorkspaceMembership,
            {"workspace_id": project.workspace_id, "principal_id": principal_id},
        )
        if (
            principal is None
            or not principal.is_active
            or membership is None
            or membership.role != "admin"
            or principal.subject != manifest.approval_policy.owner
        ):
            raise PersistencePolicyError(
                "project registration requires its active workspace admin owner"
            )

        source_id = f"project-manifest:{manifest.project_id}"
        previous = (
            self.db.execute(
                select(ContextSource)
                .where(
                    ContextSource.project_id == project.id,
                    ContextSource.source_id == source_id,
                )
                .order_by(ContextSource.version.desc())
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if previous is not None:
            bound = previous.metadata_json
            if (
                bound.get("repository_root") != str(root)
                or bound.get("repository_remote") != manifest.repository.remote
                or bound.get("context_owner") != principal.subject
                or bound.get("workspace_id") != str(project.workspace_id)
            ):
                raise PersistencePolicyError("project repository identity drifted")
        if previous is not None and previous.content_hash == manifest.manifest_hash:
            self.db.commit()
            return self._project_result(project, previous, replayed=True)

        version = 1 if previous is None else previous.version + 1
        if previous is not None:
            previous.status = "SUPERSEDED"
        projection = ContextSource(
            project_id=project.id,
            source_id=source_id,
            version=version,
            content_hash=manifest.manifest_hash,
            load_tier="MUST_LOAD",
            classification="INTERNAL",
            status="ACTIVE",
            supersedes_source_id=None if previous is None else previous.id,
            provider_policy={
                "local_allowed_classifications": list(
                    manifest.provider_data_policy.local_allowed_classifications
                ),
                "remote_allowed_classifications": list(
                    manifest.provider_data_policy.remote_allowed_classifications
                ),
            },
            token_cost=0,
            metadata_json={
                "approval_policy": {
                    "approvers": list(manifest.approval_policy.approvers),
                    "human_required_for": list(
                        manifest.approval_policy.human_required_for
                    ),
                    "owner": manifest.approval_policy.owner,
                },
                "context_sources": [
                    {"load_tier": item.load_tier, "path": item.path}
                    for item in manifest.context_sources
                ],
                "kind": "project_manifest_projection",
                "context_owner": principal.subject,
                "manifest_hash": manifest.manifest_hash,
                "manifest_path": str(manifest_path.relative_to(root)),
                "repository_remote": manifest.repository.remote,
                "repository_root": str(root),
                "schema_version": manifest.schema_version,
                "token_budget": {
                    "context_window": manifest.token_budget.context_window,
                    "reserved_output": manifest.token_budget.reserved_output,
                    "safety_margin": manifest.token_budget.safety_margin,
                },
                "workspace_id": str(project.workspace_id),
            },
        )
        self.db.add(projection)
        self.db.flush()
        self.db.commit()
        return self._project_result(project, projection, replayed=False)

    @staticmethod
    def _project_result(
        project: Project, source: ContextSource, *, replayed: bool
    ) -> Mapping[str, Any]:
        return {
            "context_source_id": str(source.id),
            "manifest_hash": source.content_hash,
            "project_id": str(project.id),
            "replayed": replayed,
            "validated": True,
            "version": source.version,
            "workspace_id": str(project.workspace_id),
        }

    def propose_knowledge(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None,
        stable_key: str,
        content: str,
        source_ids: Sequence[str],
        evidence_ids: Sequence[str],
        owner_id: uuid.UUID,
        scope: str,
        confidence: float,
        classification: str,
        proposed_by: Actor,
        valid_from: datetime,
        valid_until: datetime | None = None,
        supersedes_revision_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> Mapping[str, Any]:
        """Create a versioned proposal and an explicit conflict when needed."""

        created_at = now or _utc_now()
        owner = self.db.get(Principal, owner_id)
        if owner is None:
            raise PersistencePolicyError("knowledge owner is not registered")
        if project_id is not None:
            project = self.db.get(Project, project_id)
            if project is None or project.workspace_id != workspace_id:
                raise PersistencePolicyError("knowledge project scope is invalid")
            self._validate_knowledge_references(
                project_id=project_id,
                source_ids=source_ids,
                evidence_ids=evidence_ids,
            )
        item = self.db.execute(
            select(KnowledgeItem)
            .where(
                KnowledgeItem.workspace_id == workspace_id,
                KnowledgeItem.project_id.is_(None)
                if project_id is None
                else KnowledgeItem.project_id == project_id,
                KnowledgeItem.stable_key == stable_key,
            )
            .with_for_update()
        ).scalar_one_or_none()
        new_item = item is None
        if item is None:
            item = KnowledgeItem(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                stable_key=stable_key,
                scope=scope,
                classification=classification,
                owner_id=owner_id,
            )
        elif (
            item.scope != scope
            or item.classification != classification
            or item.owner_id != owner_id
        ):
            raise PersistencePolicyError("knowledge item metadata drifted")

        predecessor: KnowledgeRevision | None = None
        if supersedes_revision_id is not None:
            predecessor = self.db.execute(
                select(KnowledgeRevision)
                .where(KnowledgeRevision.id == supersedes_revision_id)
                .with_for_update()
            ).scalar_one_or_none()
            if (
                predecessor is None
                or predecessor.knowledge_item_id != item.id
                or predecessor.status != "APPROVED"
                or item.active_revision_id != predecessor.id
            ):
                raise KnowledgePolicyError(
                    "only the active approved knowledge head can be superseded"
                )

        version = (
            int(
                self.db.scalar(
                    select(func.coalesce(func.max(KnowledgeRevision.version), 0)).where(
                        KnowledgeRevision.knowledge_item_id == item.id
                    )
                )
                or 0
            )
            + 1
        )
        revision_id = uuid.uuid4()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        validated = DomainKnowledgeRevision(
            revision_id=revision_id,
            item_id=item.id,
            version=version,
            status=KnowledgeStatus.PROPOSED,
            content=content,
            content_hash=content_hash,
            source_ids=tuple(source_ids),
            evidence_ids=tuple(evidence_ids),
            owner=owner.subject,
            scope=scope,
            confidence=confidence,
            classification=ContextDataClassification(classification),
            valid_from=valid_from,
            valid_until=valid_until,
            supersedes_revision_id=supersedes_revision_id,
            proposed_by=proposed_by,
            reviewed_by=None,
            approved_by=None,
            created_at=created_at,
        )
        if new_item:
            self.db.add(item)
            self.db.flush()
        row = KnowledgeRevision(
            id=validated.revision_id,
            knowledge_item_id=item.id,
            version=validated.version,
            status=validated.status.value,
            content=validated.content,
            content_hash=validated.content_hash,
            source_refs=list(validated.source_ids),
            evidence_refs=list(validated.evidence_ids),
            owner_id=owner_id,
            scope=validated.scope,
            classification=validated.classification.value,
            confidence=validated.confidence,
            valid_from=validated.valid_from,
            valid_to=validated.valid_until,
            supersedes_revision_id=validated.supersedes_revision_id,
            proposed_by_type=_actor_db_kind(proposed_by),
            proposed_by_id=proposed_by.actor_id,
            created_at=created_at,
        )
        self.db.add(row)
        self.db.flush()

        conflict_id: uuid.UUID | None = None
        active = (
            None
            if item.active_revision_id is None
            else self.db.get(KnowledgeRevision, item.active_revision_id)
        )
        if (
            active is not None
            and active.content_hash != content_hash
            and supersedes_revision_id != active.id
        ):
            conflict = KnowledgeConflict(
                knowledge_item_id=item.id,
                left_revision_id=active.id,
                right_revision_id=row.id,
                status="OPEN",
                reason="proposed content conflicts with active approved revision",
            )
            self.db.add(conflict)
            self.db.flush()
            conflict_id = conflict.id
        self.db.commit()
        return {
            "conflict_id": None if conflict_id is None else str(conflict_id),
            "content_hash": row.content_hash,
            "item_id": str(item.id),
            "revision_id": str(row.id),
            "status": row.status,
            "version": row.version,
        }

    def review_knowledge(
        self, revision_id: uuid.UUID, *, reviewer: Actor
    ) -> Mapping[str, Any]:
        row = self._locked_revision(revision_id)
        if row.status != "PROPOSED":
            raise KnowledgePolicyError("only proposed knowledge can be reviewed")
        item = self.db.get(KnowledgeItem, row.knowledge_item_id)
        if item is None:
            raise PersistencePolicyError("knowledge item is missing")
        policy = self._project_approval_policy(item)
        self._require_registered_authority(
            reviewer, policy, workspace_id=item.workspace_id
        )
        proposer = _actor(row.proposed_by_type, row.proposed_by_id)
        if reviewer == proposer:
            raise KnowledgePolicyError("proposer cannot review its own knowledge")
        validated = dataclasses.replace(
            self._domain_revision(row),
            status=KnowledgeStatus.REVIEWED,
            reviewed_by=reviewer,
        )
        row.status = validated.status.value
        row.reviewed_by_type = _actor_db_kind(reviewer)
        row.reviewed_by_id = reviewer.actor_id
        self.db.commit()
        return {
            "content_hash": row.content_hash,
            "revision_id": str(row.id),
            "status": row.status,
            "version": row.version,
        }

    def approve_knowledge(
        self,
        revision_id: uuid.UUID,
        *,
        approver: Actor,
        now: datetime | None = None,
    ) -> Mapping[str, Any]:
        transition_at = now or _utc_now()
        revision = self.db.get(KnowledgeRevision, revision_id)
        if revision is None:
            raise KnowledgePolicyError("unknown knowledge revision")
        item = self.db.execute(
            select(KnowledgeItem)
            .where(KnowledgeItem.id == revision.knowledge_item_id)
            .with_for_update()
        ).scalar_one()
        policy = self._project_approval_policy(item)
        row = self._locked_revision(revision_id)
        if row.status != "REVIEWED":
            raise KnowledgePolicyError("only reviewed knowledge can be approved")
        self._require_registered_authority(
            approver, policy, workspace_id=item.workspace_id
        )
        proposed_by = _actor(row.proposed_by_type, row.proposed_by_id)
        reviewed_by = _actor(row.reviewed_by_type, row.reviewed_by_id)
        if policy.require_independent_approval and approver in {
            proposed_by,
            reviewed_by,
        }:
            raise KnowledgePolicyError("knowledge approval must be independent")
        if self.db.scalar(
            select(func.count())
            .select_from(KnowledgeConflict)
            .where(
                KnowledgeConflict.right_revision_id == row.id,
                KnowledgeConflict.status == "OPEN",
            )
        ):
            raise KnowledgePolicyError(
                "conflicting knowledge must be resolved before approval"
            )

        predecessor: KnowledgeRevision | None = None
        if row.supersedes_revision_id is not None:
            predecessor = self.db.execute(
                select(KnowledgeRevision)
                .where(KnowledgeRevision.id == row.supersedes_revision_id)
                .with_for_update()
            ).scalar_one_or_none()
            if (
                predecessor is None
                or predecessor.status != "APPROVED"
                or item.active_revision_id != predecessor.id
            ):
                raise KnowledgePolicyError(
                    "stale predecessor: approved knowledge head changed"
                )
        elif item.active_revision_id is not None:
            raise KnowledgePolicyError(
                "stale predecessor: proposal did not bind approved knowledge head"
            )

        validated = dataclasses.replace(
            self._domain_revision(row),
            status=KnowledgeStatus.APPROVED,
            approved_by=approver,
        )
        if predecessor is not None:
            terminal = dataclasses.replace(
                self._domain_revision(predecessor),
                status=KnowledgeStatus.SUPERSEDED,
                terminal_by=approver,
                terminal_at=transition_at,
            )
            predecessor.status = terminal.status.value
            predecessor.terminal_by_type = _actor_db_kind(approver)
            predecessor.terminal_by_id = approver.actor_id
            predecessor.terminal_at = terminal.terminal_at
            self.db.flush([predecessor])
        row.status = validated.status.value
        row.approved_by_type = _actor_db_kind(approver)
        row.approved_by_id = approver.actor_id
        item.active_revision_id = row.id
        self.db.commit()
        return {
            "active_revision_id": str(item.active_revision_id),
            "content_hash": row.content_hash,
            "revision_id": str(row.id),
            "status": row.status,
            "superseded_revision_id": (
                None if predecessor is None else str(predecessor.id)
            ),
            "version": row.version,
        }

    def _locked_revision(self, revision_id: uuid.UUID) -> KnowledgeRevision:
        row = self.db.execute(
            select(KnowledgeRevision)
            .where(KnowledgeRevision.id == revision_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise KnowledgePolicyError("unknown knowledge revision")
        return row

    def _domain_revision(self, row: KnowledgeRevision) -> DomainKnowledgeRevision:
        owner = self.db.get(Principal, row.owner_id)
        proposed_by = _actor(row.proposed_by_type, row.proposed_by_id)
        if owner is None or proposed_by is None:
            raise PersistencePolicyError("knowledge provenance is incomplete")
        return DomainKnowledgeRevision(
            revision_id=row.id,
            item_id=row.knowledge_item_id,
            version=row.version,
            status=KnowledgeStatus(row.status),
            content=row.content,
            content_hash=row.content_hash,
            source_ids=tuple(row.source_refs),
            evidence_ids=tuple(row.evidence_refs),
            owner=owner.subject,
            scope=row.scope,
            confidence=row.confidence,
            classification=ContextDataClassification(row.classification),
            valid_from=row.valid_from,
            valid_until=row.valid_to,
            supersedes_revision_id=row.supersedes_revision_id,
            proposed_by=proposed_by,
            reviewed_by=_actor(row.reviewed_by_type, row.reviewed_by_id),
            approved_by=_actor(row.approved_by_type, row.approved_by_id),
            created_at=row.created_at,
            terminal_by=_actor(row.terminal_by_type, row.terminal_by_id),
            terminal_at=row.terminal_at,
        )

    def _project_approval_policy(self, item: KnowledgeItem) -> ApprovalPolicy:
        if item.project_id is None:
            raise PersistencePolicyError(
                "workspace knowledge requires a configured approval authority"
            )
        source = (
            self.db.execute(
                select(ContextSource)
                .where(
                    ContextSource.project_id == item.project_id,
                    ContextSource.source_id == f"project-manifest:{item.project_id}",
                    ContextSource.status == "ACTIVE",
                )
                .order_by(ContextSource.version.desc())
            )
            .scalars()
            .first()
        )
        raw = None if source is None else source.metadata_json.get("approval_policy")
        if not isinstance(raw, Mapping):
            raise PersistencePolicyError("project approval policy is not registered")
        owner = raw.get("owner")
        approvers = raw.get("approvers")
        if (
            not isinstance(owner, str)
            or not isinstance(approvers, list)
            or not all(isinstance(value, str) and value.strip() for value in approvers)
        ):
            raise PersistencePolicyError("project approval policy is invalid")
        humans = {owner}
        policies: set[str] = set()
        for value in approvers:
            if value.startswith("policy:"):
                policies.add(value.removeprefix("policy:"))
            else:
                humans.add(value)
        return ApprovalPolicy(
            authorized_humans=frozenset(humans),
            authorized_policies=frozenset(policies),
        )

    def _require_registered_authority(
        self,
        actor: Actor,
        policy: ApprovalPolicy,
        *,
        workspace_id: uuid.UUID,
    ) -> None:
        if actor.kind not in {ActorKind.HUMAN, ActorKind.POLICY}:
            raise KnowledgePolicyError("model actors cannot be knowledge authorities")
        if not policy.permits(actor):
            raise KnowledgePolicyError("actor is not authorized by project policy")
        if actor.kind is ActorKind.HUMAN:
            principal = self.db.execute(
                select(Principal).where(
                    Principal.subject == actor.actor_id,
                    Principal.is_active.is_(True),
                )
            ).scalar_one_or_none()
            if principal is None:
                raise KnowledgePolicyError(
                    "human authority is not an active registered principal"
                )
            if self.authenticated_principal_id != principal.id:
                raise KnowledgePolicyError(
                    "human authority is not the authenticated principal"
                )
            membership = self.db.get(
                WorkspaceMembership,
                {"workspace_id": workspace_id, "principal_id": principal.id},
            )
            if membership is None or membership.role not in {"admin", "member"}:
                raise KnowledgePolicyError(
                    "human authority is not a project workspace member"
                )
        elif actor.actor_id not in self.trusted_policy_ids:
            raise KnowledgePolicyError(
                "policy authority is not trusted by this service"
            )

    def _validate_knowledge_references(
        self,
        *,
        project_id: uuid.UUID,
        source_ids: Sequence[str],
        evidence_ids: Sequence[str],
    ) -> None:
        for reference in source_ids:
            prefix = "context:"
            if not reference.startswith(prefix):
                raise PersistencePolicyError(
                    "knowledge source must reference registered context"
                )
            try:
                source_id = uuid.UUID(reference.removeprefix(prefix))
            except ValueError as exc:
                raise PersistencePolicyError(
                    "knowledge context source reference is invalid"
                ) from exc
            source = self.db.get(ContextSource, source_id)
            if (
                source is None
                or source.project_id != project_id
                or source.status != "ACTIVE"
            ):
                raise PersistencePolicyError(
                    "knowledge context source is not active project authority"
                )
        for reference in evidence_ids:
            receipt = self._verified_work_receipt(reference, project_id)
            if receipt.receipt_type not in {"verify", "close"}:
                raise PersistencePolicyError(
                    "knowledge evidence is not a verification receipt"
                )

    def register_provider(
        self, record: ProviderRecord, *, commit: bool = True
    ) -> Mapping[str, Any]:
        if provider_config_hash(record) != record.config_hash:
            raise PersistencePolicyError("provider config hash mismatch")
        row = self.db.get(ProviderRegistry, record.provider_id)
        if row is None:
            row = ProviderRegistry(provider_id=record.provider_id)
            self.db.add(row)
        elif row.config_hash != record.config_hash or row.version != record.version:
            raise PersistencePolicyError("provider identity is already bound")
        row.adapter_id = record.adapter_id
        row.locality = record.locality
        row.network_required = record.network_required
        row.secret_ref = record.secret_ref
        row.health_state = record.health.value
        row.circuit_open = record.circuit_open
        row.config_hash = record.config_hash
        row.version = record.version
        row.metadata_json = _plain(record.metadata)
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return {
            "config_hash": row.config_hash,
            "health": row.health_state,
            "metadata_only": True,
            "provider_id": row.provider_id,
            "version": row.version,
        }

    def register_model(
        self, record: ModelRecord, *, commit: bool = True
    ) -> Mapping[str, Any]:
        if (
            record.embedding_profile is not None
            and embedding_config_hash(record.embedding_profile)
            != record.embedding_profile.config_hash
        ):
            raise PersistencePolicyError("embedding profile config hash mismatch")
        if model_config_hash(record) != record.config_hash:
            raise PersistencePolicyError("model config hash mismatch")
        provider = self.db.get(ProviderRegistry, record.provider_id)
        if provider is None:
            raise PersistencePolicyError("model provider is not registered")
        provider_domain = self._provider_from_row(provider)
        fallbacks: list[ModelRecord] = []
        providers = {provider_domain.provider_id: provider_domain}
        for fallback_id in record.fallback_model_ids:
            fallback_row = self.db.get(ModelRegistry, fallback_id)
            if fallback_row is None:
                raise PersistencePolicyError("model fallback is not registered")
            fallback = self._model_from_row(fallback_row)
            fallbacks.append(dataclasses.replace(fallback, fallback_model_ids=()))
            if fallback.provider_id not in providers:
                fallback_provider = self.db.get(ProviderRegistry, fallback.provider_id)
                if fallback_provider is None:
                    raise PersistencePolicyError(
                        "model fallback provider is not registered"
                    )
                providers[fallback.provider_id] = self._provider_from_row(
                    fallback_provider
                )
        ProviderModelRegistry(providers.values(), [record, *fallbacks])
        row = self.db.get(ModelRegistry, record.model_id)
        if row is None:
            row = ModelRegistry(model_id=record.model_id)
            self.db.add(row)
        elif row.config_hash != record.config_hash or row.version != record.version:
            raise PersistencePolicyError("model identity is already bound")
        row.provider_id = record.provider_id
        row.capabilities = sorted(value.value for value in record.capabilities)
        row.context_limit = record.context_limit
        row.output_limit = record.output_limit
        row.data_classifications = sorted(value.value for value in record.allowed_data)
        row.embedding_profile = _embedding_payload(record)
        row.benchmark = dataclasses.asdict(record.benchmark)
        row.accessible = record.accessible
        row.fallback_model_ids = list(record.fallback_model_ids)
        row.config_hash = record.config_hash
        row.version = record.version
        row.health_state = record.health.value
        row.circuit_open = record.circuit_open
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return {
            "config_hash": row.config_hash,
            "data_classifications": row.data_classifications,
            "metadata_only": True,
            "model_id": row.model_id,
            "provider_id": row.provider_id,
            "version": row.version,
        }

    def register_provider_model(
        self, provider: ProviderRecord, model: ModelRecord
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        """Persist a provider/model pair as one all-or-nothing registry update."""

        try:
            provider_result = self.register_provider(provider, commit=False)
            model_result = self.register_model(model, commit=False)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return provider_result, model_result

    @staticmethod
    def _provider_from_row(row: ProviderRegistry) -> ProviderRecord:
        return ProviderRecord(
            provider_id=row.provider_id,
            adapter_id=row.adapter_id,
            locality=row.locality,
            network_required=row.network_required,
            health=row.health_state,
            circuit_open=row.circuit_open,
            version=row.version,
            config_hash=row.config_hash,
            secret_ref=row.secret_ref,
            metadata=row.metadata_json,
        )

    @staticmethod
    def _model_from_row(row: ModelRegistry) -> ModelRecord:
        profile_raw = row.embedding_profile
        profile = (
            None
            if profile_raw is None
            else EmbeddingProfile(
                profile_id=profile_raw["profile_id"],
                dimension=profile_raw["dimension"],
                distance=DistanceMetric(profile_raw["distance"]),
                query_prefix=profile_raw["query_prefix"],
                document_prefix=profile_raw["document_prefix"],
                config_hash=profile_raw["config_hash"],
            )
        )
        benchmark = row.benchmark
        return ModelRecord(
            model_id=row.model_id,
            provider_id=row.provider_id,
            capabilities=frozenset(Capability(value) for value in row.capabilities),
            context_limit=row.context_limit,
            output_limit=row.output_limit,
            allowed_data=frozenset(
                DataClassification(value) for value in row.data_classifications
            ),
            benchmark=Benchmark(
                latency_p95_ms=benchmark["latency_p95_ms"],
                cost_micro_usd=benchmark["cost_micro_usd"],
                quality_score=benchmark["quality_score"],
                sample_size=benchmark["sample_size"],
            ),
            version=row.version,
            config_hash=row.config_hash,
            health=HealthState(row.health_state),
            circuit_open=row.circuit_open,
            accessible=row.accessible,
            embedding_profile=profile,
            fallback_model_ids=tuple(row.fallback_model_ids),
        )

    def register_skill(
        self,
        record: SkillRecord,
    ) -> Mapping[str, Any]:
        """Persist validated metadata only through workspace and receipt authority."""

        registered_by_principal_id = self.authenticated_principal_id
        if registered_by_principal_id is None:
            raise PersistencePolicyError(
                "skill registration requires an authenticated principal"
            )
        if record.enabled_scope.value != "project" or record.enabled_scope_id is None:
            raise PersistencePolicyError(
                "skill registration requires an exact project scope"
            )
        try:
            project_id = uuid.UUID(record.enabled_scope_id)
        except ValueError as exc:
            raise PersistencePolicyError("skill project scope is invalid") from exc
        project = self.db.execute(
            select(Project).where(Project.id == project_id).with_for_update()
        ).scalar_one_or_none()
        principal = self.db.get(Principal, registered_by_principal_id)
        membership = (
            None
            if project is None
            else self.db.get(
                WorkspaceMembership,
                {
                    "workspace_id": project.workspace_id,
                    "principal_id": registered_by_principal_id,
                },
            )
        )
        if (
            project is None
            or project.deleted_at is not None
            or principal is None
            or not principal.is_active
            or membership is None
            or membership.role != "admin"
            or principal.subject != record.owner
        ):
            raise PersistencePolicyError(
                "skill registration requires its active workspace admin owner"
            )
        manifest = self._active_project_manifest_for_id(project.id)
        approval = manifest.metadata_json.get("approval_policy")
        if (
            not isinstance(approval, Mapping)
            or approval.get("owner") != principal.subject
        ):
            raise PersistencePolicyError(
                "skill owner is not bound to the active project manifest"
            )
        for reference in record.operation_receipt_refs:
            receipt = self._verified_work_receipt(reference, project.id)
            if receipt.receipt_type not in {"apply", "close"}:
                raise PersistencePolicyError(
                    "skill operation receipt is not an applied operation"
                )
            self._require_skill_receipt_binding(receipt, record)
        security_receipt = self._verified_work_receipt(
            record.security_scan_receipt_ref, project.id
        )
        if security_receipt.receipt_type != "verify":
            raise PersistencePolicyError(
                "skill security scan receipt is not a verification"
            )
        self._require_skill_receipt_binding(security_receipt, record)
        row = self.db.get(SkillRegistry, record.skill_id)
        if row is None:
            row = SkillRegistry(skill_id=record.skill_id)
            self.db.add(row)
        elif (
            row.package_hash != record.package_hash
            or row.commit_sha != record.commit_sha
            or (row.receipt_refs or {}).get("record_hash") != record.record_hash
        ):
            raise PersistencePolicyError("skill identity is already bound")
        row.source_uri = record.source_uri
        row.version = record.version
        row.commit_sha = record.commit_sha
        row.package_hash = record.package_hash
        row.publisher = record.publisher
        row.owner = record.owner
        row.trust_level = record.trust_level.value
        row.required_tools = sorted(record.required_tools)
        row.required_permissions = sorted(record.required_permissions)
        row.network_scope = {"required": record.network_required}
        row.filesystem_scope = list(record.filesystem_scopes)
        row.supported_adapters = sorted(record.supported_adapters)
        row.receipt_refs = {
            "operation": list(record.operation_receipt_refs),
            "record_hash": record.record_hash,
        }
        row.license_id = record.license_id
        row.security_scan_receipt = record.security_scan_receipt_ref
        row.enabled_scope_type = record.enabled_scope.value
        row.enabled_scope_id = record.enabled_scope_id
        row.enabled = record.enabled
        self.db.commit()
        return {
            "package_hash": row.package_hash,
            "record_hash": record.record_hash,
            "skill_id": row.skill_id,
        }

    def _verified_work_receipt(
        self, reference: str, project_id: uuid.UUID
    ) -> WorkReceipt:
        prefix = "receipt://work/"
        if not reference.startswith(prefix):
            raise PersistencePolicyError("skill receipt must reference a work receipt")
        try:
            receipt_id = uuid.UUID(reference.removeprefix(prefix))
        except ValueError as exc:
            raise PersistencePolicyError(
                "skill work receipt reference is invalid"
            ) from exc
        receipt = self.db.get(WorkReceipt, receipt_id)
        work_item = (
            None if receipt is None else self.db.get(WorkItem, receipt.work_item_id)
        )
        if (
            receipt is None
            or receipt.exit_status != 0
            or work_item is None
            or work_item.project_id != project_id
        ):
            raise PersistencePolicyError(
                "skill work receipt is not successful project evidence"
            )
        return receipt

    @staticmethod
    def _require_skill_receipt_binding(
        receipt: WorkReceipt, record: SkillRecord
    ) -> None:
        required = {
            "commit_sha": record.commit_sha,
            "package_hash": record.package_hash,
            "skill_record_hash": record.record_hash,
        }
        if (
            receipt.output_artifact_hash != record.package_hash
            or not isinstance(receipt.attestation, Mapping)
            or any(
                receipt.attestation.get(key) != value for key, value in required.items()
            )
        ):
            raise PersistencePolicyError(
                "skill receipt is not bound to the exact registry record"
            )

    def verify_registered_skill(
        self,
        skill_id: str,
        *,
        observed_package_hash: str,
        adapter_id: str,
        policy: SkillAdmissionPolicy,
        requested_permissions: frozenset[str] = frozenset(),
    ) -> SkillProjection:
        """Admit only a previously persisted registry record.

        Registration and execution admission are intentionally separate trust
        boundaries: an execution request cannot supply its own identity,
        receipts or trust level.
        """

        row = self.db.execute(
            select(SkillRegistry)
            .where(SkillRegistry.skill_id == skill_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise PersistencePolicyError("skill is not registered")
        record = self._skill_from_row(row)
        stored_hash = (row.receipt_refs or {}).get("record_hash")
        if stored_hash != record.record_hash:
            raise PersistencePolicyError("skill registry record hash drifted")
        return DomainSkillRegistry([record]).admit_execution(
            skill_id,
            observed_package_hash=observed_package_hash,
            adapter_id=adapter_id,
            policy=policy,
            requested_permissions=requested_permissions,
        )

    @staticmethod
    def _skill_from_row(row: SkillRegistry) -> SkillRecord:
        from src.context_vault.registry import SkillScope, TrustLevel

        receipt_refs = row.receipt_refs or {}
        operation = receipt_refs.get("operation")
        if not isinstance(operation, list):
            raise PersistencePolicyError("skill operation receipts are missing")
        network_scope = row.network_scope or {}
        if not isinstance(network_scope, Mapping) or not isinstance(
            network_scope.get("required"), bool
        ):
            raise PersistencePolicyError("skill network scope is invalid")
        return SkillRecord(
            skill_id=row.skill_id,
            source_uri=row.source_uri,
            version=row.version,
            commit_sha=row.commit_sha,
            package_hash=row.package_hash,
            publisher=row.publisher,
            owner=row.owner,
            trust_level=TrustLevel(row.trust_level),
            required_tools=frozenset(row.required_tools),
            required_permissions=frozenset(row.required_permissions),
            network_required=network_scope["required"],
            filesystem_scopes=tuple(row.filesystem_scope),
            supported_adapters=frozenset(row.supported_adapters),
            operation_receipt_refs=tuple(operation),
            security_scan_receipt_ref=row.security_scan_receipt,
            license_id=row.license_id,
            enabled_scope=SkillScope(row.enabled_scope_type),
            enabled_scope_id=row.enabled_scope_id,
            enabled=row.enabled,
        )

    def bind_compiled_context(
        self,
        *,
        compiled: DomainCompiledContext,
        projections: Sequence[AdapterProjection],
        model_id: str,
        context_window: int,
        reserved_output: int,
        safety_margin: int,
    ) -> Mapping[str, Any]:
        try:
            with self.db.begin_nested():
                result = self._bind_compiled_context(
                    compiled=compiled,
                    projections=projections,
                    model_id=model_id,
                    context_window=context_window,
                    reserved_output=reserved_output,
                    safety_margin=safety_margin,
                )
        except Exception:
            raise
        self.db.commit()
        return result

    def _bind_compiled_context(
        self,
        *,
        compiled: DomainCompiledContext,
        projections: Sequence[AdapterProjection],
        model_id: str,
        context_window: int,
        reserved_output: int,
        safety_margin: int,
    ) -> Mapping[str, Any]:
        """Atomically bind immutable core and adapter hashes to one work attempt."""

        try:
            attempt_id = uuid.UUID(compiled.attempt_id)
        except ValueError as exc:
            raise PersistencePolicyError("compiled attempt_id must be a UUID") from exc
        attempt = self.db.execute(
            select(WorkAttempt).where(WorkAttempt.id == attempt_id).with_for_update()
        ).scalar_one_or_none()
        if attempt is None:
            raise PersistencePolicyError("compiled work attempt is not registered")
        item = self.db.execute(
            select(WorkItem)
            .where(WorkItem.id == attempt.work_item_id)
            .with_for_update()
        ).scalar_one()
        claim = self.db.execute(
            select(WorkClaim)
            .where(WorkClaim.attempt_id == attempt.id)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            claim is None
            or claim.status != "active"
            or claim.expires_at <= _utc_now()
            or attempt.status != "CLAIMED"
            or item.active_attempt_id != attempt.id
            or item.active_fencing_token != claim.fencing_token
            or item.expected_revision != attempt.expected_revision
        ):
            raise PersistencePolicyError(
                "compiled context requires the current active claim"
            )
        provider = self.db.get(ProviderRegistry, compiled.provider_id)
        model = self.db.get(ModelRegistry, model_id)
        if (
            provider is None
            or model is None
            or model.provider_id != provider.provider_id
        ):
            raise PersistencePolicyError("compiled provider/model identity is invalid")
        if attempt.provider_id not in {None, compiled.provider_id}:
            raise PersistencePolicyError("attempt provider identity drifted")
        if attempt.model_id not in {None, model_id}:
            raise PersistencePolicyError("attempt model identity drifted")
        canonical_payload = compiled.canonical_payload()
        if canonical_hash(canonical_payload) != compiled.manifest_hash:
            raise PersistencePolicyError("compiled context hash mismatch")
        budget = canonical_payload.get("budget")
        if not isinstance(budget, Mapping) or (
            budget.get("context_window") != context_window
            or budget.get("reserved_output") != reserved_output
            or budget.get("safety_margin") != safety_margin
        ):
            raise PersistencePolicyError("compiled context budget binding drifted")
        if not projections:
            raise PersistencePolicyError("adapter projection is required")
        assert_projection_conformance(
            CoreContextEnvelope.from_compiled_context(compiled), tuple(projections)
        )
        provider_binding = canonical_payload.get("provider")
        if not isinstance(provider_binding, Mapping) or (
            provider_binding.get("provider_id") != provider.provider_id
            or provider_binding.get("is_remote") != (provider.locality == "remote")
        ):
            raise PersistencePolicyError("compiled provider locality binding drifted")
        allowed = provider_binding.get("allowed_classifications")
        if not isinstance(allowed, list) or not set(allowed).issubset(
            set(model.data_classifications)
        ):
            raise PersistencePolicyError("compiled model data policy is not admitted")
        raw_context_items = canonical_payload.get("items")
        if not isinstance(raw_context_items, list) or any(
            not isinstance(context_item, Mapping)
            or context_item.get("classification") not in allowed
            for context_item in raw_context_items
        ):
            raise PersistencePolicyError(
                "compiled source classification is outside provider policy"
            )
        manifest_source = self._active_project_manifest(item)
        locality_key = (
            "remote_allowed_classifications"
            if provider.locality == "remote"
            else "local_allowed_classifications"
        )
        persisted_allowed = manifest_source.provider_policy.get(locality_key)
        persisted_budget = manifest_source.metadata_json.get("token_budget")
        if (
            not isinstance(persisted_allowed, list)
            or not set(allowed).issubset(set(persisted_allowed))
            or not isinstance(persisted_budget, Mapping)
            or persisted_budget.get("context_window") != context_window
            or persisted_budget.get("reserved_output") != reserved_output
            or persisted_budget.get("safety_margin") != safety_margin
        ):
            raise PersistencePolicyError(
                "compiled context is not bound to active project policy"
            )
        if (
            not model.accessible
            or model.health_state != "healthy"
            or model.circuit_open
            or provider.health_state != "healthy"
            or provider.circuit_open
            or context_window > model.context_limit
            or reserved_output > model.output_limit
            or "chat" not in model.capabilities
        ):
            raise PersistencePolicyError("compiled provider/model route is not healthy")
        self._validate_compiled_sources(
            item=item,
            canonical_payload=canonical_payload,
        )
        if attempt.input_context_manifest_hash not in {
            None,
            compiled.manifest_hash,
        }:
            raise PersistencePolicyError("attempt context manifest is already bound")

        row = self.db.execute(
            select(ContextManifest)
            .where(ContextManifest.attempt_id == attempt.id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            row = ContextManifest(
                attempt_id=attempt.id,
                manifest_hash=compiled.manifest_hash,
                schema_version=str(compiled.schema_version),
                provider_id=compiled.provider_id,
                model_id=model_id,
                token_budget=context_window,
                reserved_output=reserved_output,
                safety_margin=safety_margin,
                core_json=canonical_payload,
            )
            self.db.add(row)
            self.db.flush()
        elif (
            row.manifest_hash != compiled.manifest_hash
            or row.provider_id != compiled.provider_id
            or row.model_id != model_id
            or row.core_json != canonical_payload
        ):
            raise PersistencePolicyError("attempt context manifest binding drifted")
        attempt.input_context_manifest_hash = compiled.manifest_hash
        attempt.provider_id = compiled.provider_id
        attempt.model_id = model_id

        hashes: dict[str, str] = {}
        for projection in projections:
            if projection.core_manifest_hash != compiled.manifest_hash:
                raise PersistencePolicyError("adapter projection changed core manifest")
            if projection.work_authority.attempt_id != compiled.attempt_id:
                raise PersistencePolicyError(
                    "adapter projection belongs to another attempt"
                )
            authority = projection.work_authority
            if (
                projection.model_id != model_id
                or authority.work_item_id != str(item.id)
                or authority.claim_id != str(claim.id)
                or authority.fencing_token != claim.fencing_token
                or authority.current_revision != item.expected_revision
            ):
                raise PersistencePolicyError(
                    "adapter projection authority binding is stale"
                )
            existing = self.db.execute(
                select(CompiledContext).where(
                    CompiledContext.context_manifest_id == row.id,
                    CompiledContext.adapter_id == projection.adapter_id,
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = CompiledContext(
                    context_manifest_id=row.id,
                    adapter_id=projection.adapter_id,
                    rendered_hash=projection.projection_receipt_hash,
                    token_cost=compiled.token_cost,
                )
                self.db.add(existing)
            elif existing.rendered_hash != projection.projection_receipt_hash:
                raise PersistencePolicyError("adapter compiled context hash drifted")
            hashes[projection.adapter_id] = projection.projection_receipt_hash
        self.db.flush()
        return {
            "adapter_hashes": dict(sorted(hashes.items())),
            "attempt_id": str(attempt.id),
            "manifest_hash": row.manifest_hash,
        }

    def _validate_compiled_sources(
        self,
        *,
        item: WorkItem,
        canonical_payload: Mapping[str, Any],
    ) -> None:
        if item.project_id is None:
            raise PersistencePolicyError("compiled work item must belong to a project")
        raw_items = canonical_payload.get("items")
        if not isinstance(raw_items, list):
            raise PersistencePolicyError("compiled context items are invalid")
        included_source_ids = {
            source.get("source_id")
            for source in raw_items
            if isinstance(source, Mapping)
        }
        mandatory_ids = set(
            self.db.scalars(
                select(ContextSource.source_id).where(
                    ContextSource.project_id == item.project_id,
                    ContextSource.status == "ACTIVE",
                    ContextSource.load_tier == "MUST_LOAD",
                    ~ContextSource.source_id.startswith("project-manifest:"),
                )
            )
        )
        if not mandatory_ids.issubset(included_source_ids):
            raise PersistencePolicyError(
                "compiled context omitted an authoritative MUST_LOAD source"
            )
        work_payload = {
            "acceptance_criteria": item.acceptance_criteria,
            "attempt_id": str(item.active_attempt_id),
            "claim_id": str(
                self.db.scalar(
                    select(WorkClaim.id).where(
                        WorkClaim.attempt_id == item.active_attempt_id,
                        WorkClaim.status == "active",
                    )
                )
            ),
            "evidence_requirements": item.evidence_requirements,
            "expected_revision": item.expected_revision,
            "fencing_token": item.active_fencing_token,
            "objective": item.objective,
            "revision": item.revision,
            "scope": item.scope_json,
            "status": item.status,
            "work_item_id": str(item.id),
        }
        work_content = json.dumps(
            work_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        active_work = next(
            (
                source
                for source in raw_items
                if isinstance(source, Mapping)
                and source.get("source_id") == f"work-item:{item.id}"
            ),
            None,
        )
        if (
            active_work is None
            or active_work.get("logical_id") != "active-work"
            or active_work.get("version") != item.revision
            or active_work.get("role") != "ACTIVE_WORK"
            or active_work.get("load_tier") != "MUST_LOAD"
            or active_work.get("classification") != "INTERNAL"
            or active_work.get("content") != work_content
            or active_work.get("hash")
            != hashlib.sha256(work_content.encode()).hexdigest()
        ):
            raise PersistencePolicyError(
                "compiled context omitted exact active work authority"
            )
        for source in raw_items:
            if not isinstance(source, Mapping):
                raise PersistencePolicyError("compiled context source is invalid")
            source_id = source.get("source_id")
            if not isinstance(source_id, str):
                raise PersistencePolicyError("compiled source_id is invalid")
            if source_id == f"work-item:{item.id}":
                continue
            if source_id.startswith("knowledge:"):
                try:
                    revision_id = uuid.UUID(source_id.removeprefix("knowledge:"))
                except ValueError as exc:
                    raise PersistencePolicyError(
                        "compiled knowledge source_id is invalid"
                    ) from exc
                revision = self.db.get(KnowledgeRevision, revision_id)
                knowledge_item = (
                    None
                    if revision is None
                    else self.db.get(KnowledgeItem, revision.knowledge_item_id)
                )
                admitted = (
                    revision is not None
                    and knowledge_item is not None
                    and knowledge_item.project_id == item.project_id
                    and knowledge_item.active_revision_id == revision.id
                    and revision.status == "APPROVED"
                    and revision.valid_from <= _utc_now()
                    and (revision.valid_to is None or _utc_now() < revision.valid_to)
                    and source.get("version") == revision.version
                    and source.get("hash") == revision.content_hash
                    and source.get("classification") == revision.classification
                )
            else:
                context_source = self.db.execute(
                    select(ContextSource).where(
                        ContextSource.project_id == item.project_id,
                        ContextSource.source_id == source_id,
                        ContextSource.version == source.get("version"),
                    )
                ).scalar_one_or_none()
                admitted = (
                    context_source is not None
                    and context_source.status == "ACTIVE"
                    and context_source.content_hash == source.get("hash")
                    and context_source.load_tier == source.get("load_tier")
                    and context_source.classification == source.get("classification")
                )
            if not admitted:
                raise PersistencePolicyError(
                    "compiled context contains a non-authoritative source"
                )

    def _active_project_manifest(self, item: WorkItem) -> ContextSource:
        if item.project_id is None:
            raise PersistencePolicyError("work item does not belong to a project")
        return self._active_project_manifest_for_id(item.project_id)

    def _active_project_manifest_for_id(self, project_id: uuid.UUID) -> ContextSource:
        source = (
            self.db.execute(
                select(ContextSource)
                .where(
                    ContextSource.project_id == project_id,
                    ContextSource.source_id == f"project-manifest:{project_id}",
                    ContextSource.status == "ACTIVE",
                )
                .order_by(ContextSource.version.desc())
            )
            .scalars()
            .first()
        )
        if source is None:
            raise PersistencePolicyError("active project manifest is not registered")
        return source
