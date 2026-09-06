"""Provider-neutral projections for OpenCode, Claude, and Codex CLIs.

Adapters format an immutable core context and produce effect *intents*.  They never
write canonical state; the Work Graph repository remains responsible for applying
an intent with claim/fencing/idempotency checks and emitting its receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .handoff import VerifiedHandoff
from .registry import RegistryError, canonical_hash


_HASH = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}")


class AdapterError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class CompiledContextProtocol(Protocol):
    manifest_hash: str

    def canonical_payload(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CoreContextEnvelope:
    _canonical_payload_json: str
    manifest_hash: str

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        claimed_hash: str | None = None,
    ) -> CoreContextEnvelope:
        try:
            canonical_json = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise AdapterError("NON_CANONICAL_JSON", str(exc)) from exc
        calculated = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        if claimed_hash is not None and claimed_hash != calculated:
            raise AdapterError("CORE_MANIFEST_HASH_MISMATCH", claimed_hash)
        return cls(_canonical_payload_json=canonical_json, manifest_hash=calculated)

    @classmethod
    def from_compiled_context(
        cls,
        compiled: CompiledContextProtocol,
    ) -> CoreContextEnvelope:
        """Bind directly to ContextCompiler output without reinterpreting it."""

        return cls.from_payload(
            compiled.canonical_payload(),
            claimed_hash=compiled.manifest_hash,
        )

    @property
    def payload(self) -> dict[str, Any]:
        """Return an isolated copy; callers cannot mutate the bound manifest."""

        return json.loads(self._canonical_payload_json)

    def verify(self) -> None:
        if not _HASH.fullmatch(self.manifest_hash):
            raise AdapterError("INVALID_CORE_MANIFEST_HASH", self.manifest_hash)
        try:
            canonical_payload = json.dumps(
                self.payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise AdapterError("NON_CANONICAL_JSON", str(exc)) from exc
        if canonical_payload != self._canonical_payload_json:
            raise AdapterError("CORE_MANIFEST_NOT_CANONICAL", self.manifest_hash)
        if (
            hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
            != self.manifest_hash
        ):
            raise AdapterError("CORE_MANIFEST_MUTATED", self.manifest_hash)


@dataclass(frozen=True)
class WorkAuthority:
    work_item_id: str
    attempt_id: str
    claim_id: str
    fencing_token: int
    current_revision: str

    def __post_init__(self) -> None:
        for value in (self.work_item_id, self.attempt_id, self.claim_id):
            if not _ID.fullmatch(value):
                raise AdapterError("INVALID_WORK_AUTHORITY", value)
        if (
            not isinstance(self.fencing_token, int)
            or isinstance(self.fencing_token, bool)
            or self.fencing_token <= 0
        ):
            raise AdapterError("INVALID_FENCING_TOKEN", str(self.fencing_token))
        if not _REVISION.fullmatch(self.current_revision):
            raise AdapterError("INVALID_CURRENT_REVISION", self.current_revision)


@dataclass(frozen=True)
class AdapterProjection:
    adapter_id: str
    adapter_version: str
    core_manifest_hash: str
    work_authority: WorkAuthority
    model_id: str
    tool_mapping: Mapping[str, str]
    formatted_payload: Mapping[str, Any]
    projection_receipt_hash: str
    canonical_authority: str = "postgresql_work_graph"
    direct_state_write: bool = False
    transcript_policy: str = "optional_private_debug_noncanonical"


@dataclass(frozen=True)
class EffectIntent:
    adapter_id: str
    model_id: str
    work_item_id: str
    attempt_id: str
    claim_id: str
    fencing_token: int
    expected_revision: str
    core_manifest_hash: str
    verified_handoff_hash: str
    idempotency_key: str
    intent_hash: str
    canonical_effect_applied: bool = False


class CliAdapter:
    adapter_id = "base"
    adapter_version = "1"
    context_field = "context"

    def project(
        self,
        core: CoreContextEnvelope,
        authority: WorkAuthority,
        *,
        model_id: str,
        tool_mapping: Mapping[str, str],
    ) -> AdapterProjection:
        core.verify()
        if not _ID.fullmatch(model_id):
            raise AdapterError("MODEL_ID_NOT_EXACT", model_id)
        if any(
            not _ID.fullmatch(key) or not _ID.fullmatch(value)
            for key, value in tool_mapping.items()
        ):
            raise AdapterError("INVALID_TOOL_MAPPING", self.adapter_id)
        formatted = {
            self.context_field: core.payload,
            "context_manifest_hash": core.manifest_hash,
            "attempt_metadata": {
                "adapter_id": self.adapter_id,
                "adapter_version": self.adapter_version,
                "model_id": model_id,
            },
            "protocol": {
                "claim_id": authority.claim_id,
                "fencing_token": authority.fencing_token,
                "attempt_id": authority.attempt_id,
                "heartbeat_required": True,
                "receipt_required": True,
                "canonical_state_write": False,
                "effect_mode": "intent_only",
            },
            "tool_mapping": dict(sorted(tool_mapping.items())),
        }
        receipt_payload = {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "canonical_authority": "postgresql_work_graph",
            "core_manifest_hash": core.manifest_hash,
            "direct_state_write": False,
            "formatted_payload_hash": canonical_hash(formatted),
            "model_id": model_id,
            "tool_mapping": dict(sorted(tool_mapping.items())),
            "transcript_policy": "optional_private_debug_noncanonical",
            "work_authority": {
                "attempt_id": authority.attempt_id,
                "claim_id": authority.claim_id,
                "current_revision": authority.current_revision,
                "fencing_token": authority.fencing_token,
                "work_item_id": authority.work_item_id,
            },
        }
        return AdapterProjection(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            core_manifest_hash=core.manifest_hash,
            work_authority=authority,
            model_id=model_id,
            tool_mapping=dict(tool_mapping),
            formatted_payload=formatted,
            projection_receipt_hash=canonical_hash(receipt_payload),
        )

    def prepare_effect(
        self,
        projection: AdapterProjection,
        *,
        current_authority: WorkAuthority,
        verified_handoff: VerifiedHandoff,
        idempotency_key: str,
    ) -> EffectIntent:
        if projection.adapter_id != self.adapter_id:
            raise AdapterError("PROJECTION_ADAPTER_MISMATCH", projection.adapter_id)
        _verify_projection_invariants(projection)
        _verify_projection_semantics(
            projection, expected_context_field=self.context_field
        )
        _verify_projection_receipt(projection)
        if (
            not isinstance(verified_handoff, VerifiedHandoff)
            or not verified_handoff.is_verified
        ):
            raise AdapterError("UNVERIFIED_HANDOFF", self.adapter_id)
        if not _ID.fullmatch(idempotency_key):
            raise AdapterError("INVALID_IDEMPOTENCY_KEY", idempotency_key)
        expected = projection.work_authority
        if expected != current_authority:
            raise AdapterError("STALE_CLAIM_OR_REVISION", current_authority.claim_id)
        handoff_bindings = (
            (verified_handoff.work_item_id, expected.work_item_id),
            (verified_handoff.attempt_id, expected.attempt_id),
            (verified_handoff.claim_id, expected.claim_id),
            (verified_handoff.fencing_token, expected.fencing_token),
            (verified_handoff.current_revision, expected.current_revision),
            (verified_handoff.context_manifest_hash, projection.core_manifest_hash),
        )
        if any(actual != wanted for actual, wanted in handoff_bindings):
            raise AdapterError("HANDOFF_BINDING_MISMATCH", self.adapter_id)
        payload = {
            "adapter_id": self.adapter_id,
            "claim_id": expected.claim_id,
            "core_manifest_hash": projection.core_manifest_hash,
            "expected_revision": expected.current_revision,
            "fencing_token": expected.fencing_token,
            "idempotency_key": idempotency_key,
            "model_id": projection.model_id,
            "verified_handoff_hash": verified_handoff.handoff_hash,
            "work_item_id": expected.work_item_id,
            "attempt_id": expected.attempt_id,
        }
        return EffectIntent(
            adapter_id=self.adapter_id,
            model_id=projection.model_id,
            work_item_id=expected.work_item_id,
            attempt_id=expected.attempt_id,
            claim_id=expected.claim_id,
            fencing_token=expected.fencing_token,
            expected_revision=expected.current_revision,
            core_manifest_hash=projection.core_manifest_hash,
            verified_handoff_hash=verified_handoff.handoff_hash,
            idempotency_key=idempotency_key,
            intent_hash=canonical_hash(payload),
        )


class OpenCodeAdapter(CliAdapter):
    adapter_id = "opencode"
    context_field = "context"


class ClaudeCliAdapter(CliAdapter):
    adapter_id = "claude"
    context_field = "system_context"


class CodexCliAdapter(CliAdapter):
    adapter_id = "codex"
    context_field = "input_context"


class AdapterHub:
    def __init__(
        self,
        adapters: tuple[CliAdapter, ...],
        availability: Mapping[str, bool] | None = None,
    ) -> None:
        self._adapters = {adapter.adapter_id: adapter for adapter in adapters}
        if len(self._adapters) != len(adapters):
            raise AdapterError("DUPLICATE_ADAPTER", "adapter ids must be unique")
        self._availability = {adapter_id: True for adapter_id in self._adapters}
        self._availability.update(availability or {})

    @classmethod
    def default(cls, availability: Mapping[str, bool] | None = None) -> AdapterHub:
        return cls(
            (OpenCodeAdapter(), ClaudeCliAdapter(), CodexCliAdapter()),
            availability,
        )

    def project(
        self,
        adapter_id: str,
        core: CoreContextEnvelope,
        authority: WorkAuthority,
        *,
        model_id: str,
        tool_mapping: Mapping[str, str],
    ) -> AdapterProjection:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise AdapterError("ADAPTER_NOT_REGISTERED", adapter_id)
        if not self._availability.get(adapter_id, False):
            raise AdapterError("ADAPTER_UNAVAILABLE", adapter_id)
        return adapter.project(
            core,
            authority,
            model_id=model_id,
            tool_mapping=tool_mapping,
        )

    def prepare_effect(
        self,
        adapter_id: str,
        projection: AdapterProjection,
        *,
        current_authority: WorkAuthority,
        verified_handoff: VerifiedHandoff,
        idempotency_key: str,
    ) -> EffectIntent:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise AdapterError("ADAPTER_NOT_REGISTERED", adapter_id)
        if not self._availability.get(adapter_id, False):
            raise AdapterError("ADAPTER_UNAVAILABLE", adapter_id)
        return adapter.prepare_effect(
            projection,
            current_authority=current_authority,
            verified_handoff=verified_handoff,
            idempotency_key=idempotency_key,
        )

    def is_available(self, adapter_id: str) -> bool:
        return adapter_id in self._adapters and self._availability.get(
            adapter_id, False
        )

    @property
    def registered_adapter_ids(self) -> frozenset[str]:
        return frozenset(self._adapters)


def assert_projection_conformance(
    core: CoreContextEnvelope,
    projections: tuple[AdapterProjection, ...],
) -> None:
    """Conformance gate: formatting may differ, core semantics/hash may not."""

    core.verify()
    if not projections:
        raise AdapterError("NO_PROJECTIONS", "at least one adapter projection required")
    if len({item.adapter_id for item in projections}) != len(projections):
        raise AdapterError("DUPLICATE_PROJECTION", "adapter ids must be unique")
    for projection in projections:
        _verify_projection_invariants(projection)
        _verify_projection_semantics(projection)
        _verify_projection_receipt(projection)
        if projection.core_manifest_hash != core.manifest_hash:
            raise AdapterError("CORE_MANIFEST_DIVERGENCE", projection.adapter_id)
        projected_context = next(
            (
                projection.formatted_payload[key]
                for key in ("context", "system_context", "input_context")
                if key in projection.formatted_payload
            ),
            None,
        )
        if (
            projected_context is None
            or canonical_hash(projected_context) != core.manifest_hash
        ):
            raise AdapterError("CORE_PAYLOAD_DIVERGENCE", projection.adapter_id)


def _verify_projection_receipt(projection: AdapterProjection) -> None:
    receipt_payload = {
        "adapter_id": projection.adapter_id,
        "adapter_version": projection.adapter_version,
        "canonical_authority": projection.canonical_authority,
        "core_manifest_hash": projection.core_manifest_hash,
        "direct_state_write": projection.direct_state_write,
        "formatted_payload_hash": canonical_hash(projection.formatted_payload),
        "model_id": projection.model_id,
        "tool_mapping": dict(sorted(projection.tool_mapping.items())),
        "transcript_policy": projection.transcript_policy,
        "work_authority": {
            "attempt_id": projection.work_authority.attempt_id,
            "claim_id": projection.work_authority.claim_id,
            "current_revision": projection.work_authority.current_revision,
            "fencing_token": projection.work_authority.fencing_token,
            "work_item_id": projection.work_authority.work_item_id,
        },
    }
    if canonical_hash(receipt_payload) != projection.projection_receipt_hash:
        raise AdapterError("PROJECTION_RECEIPT_MISMATCH", projection.adapter_id)


def _verify_projection_invariants(projection: AdapterProjection) -> None:
    if not isinstance(projection.direct_state_write, bool):
        raise AdapterError("DIRECT_CANONICAL_WRITE", projection.adapter_id)
    if projection.canonical_authority != "postgresql_work_graph":
        raise AdapterError("AUTHORITY_DIVERGENCE", projection.adapter_id)
    if projection.direct_state_write:
        raise AdapterError("DIRECT_CANONICAL_WRITE", projection.adapter_id)
    if projection.transcript_policy != "optional_private_debug_noncanonical":
        raise AdapterError("TRANSCRIPT_POLICY_DIVERGENCE", projection.adapter_id)


def _verify_projection_semantics(
    projection: AdapterProjection,
    *,
    expected_context_field: str | None = None,
) -> None:
    context_fields = {
        "opencode": "context",
        "claude": "system_context",
        "codex": "input_context",
    }
    canonical_field = context_fields.get(projection.adapter_id)
    if canonical_field is None:
        raise AdapterError("ADAPTER_NOT_REGISTERED", projection.adapter_id)
    if expected_context_field is not None and canonical_field != expected_context_field:
        raise AdapterError("PROJECTION_ADAPTER_MISMATCH", projection.adapter_id)

    formatted = projection.formatted_payload
    allowed_fields = {
        canonical_field,
        "attempt_metadata",
        "context_manifest_hash",
        "protocol",
        "tool_mapping",
    }
    present_context_fields = {
        field for field in context_fields.values() if field in formatted
    }
    if len(present_context_fields) > 1:
        raise AdapterError("AMBIGUOUS_CONTEXT_PAYLOAD", projection.adapter_id)
    if present_context_fields != {canonical_field}:
        raise AdapterError("CONTEXT_FIELD_MISMATCH", projection.adapter_id)
    if set(formatted) != allowed_fields:
        raise AdapterError("FORMATTED_PAYLOAD_SCHEMA_DRIFT", projection.adapter_id)

    if not isinstance(formatted[canonical_field], Mapping):
        raise AdapterError("CORE_PAYLOAD_DIVERGENCE", projection.adapter_id)
    try:
        context_hash = canonical_hash(formatted[canonical_field])
    except RegistryError as exc:
        raise AdapterError("CORE_PAYLOAD_DIVERGENCE", projection.adapter_id) from exc
    if context_hash != projection.core_manifest_hash:
        raise AdapterError("CORE_PAYLOAD_DIVERGENCE", projection.adapter_id)
    if formatted["context_manifest_hash"] != projection.core_manifest_hash:
        raise AdapterError("CORE_MANIFEST_DIVERGENCE", projection.adapter_id)

    expected_attempt_metadata = {
        "adapter_id": projection.adapter_id,
        "adapter_version": projection.adapter_version,
        "model_id": projection.model_id,
    }
    if formatted["attempt_metadata"] != expected_attempt_metadata:
        raise AdapterError("ATTEMPT_METADATA_DIVERGENCE", projection.adapter_id)
    expected_protocol = {
        "claim_id": projection.work_authority.claim_id,
        "fencing_token": projection.work_authority.fencing_token,
        "attempt_id": projection.work_authority.attempt_id,
        "heartbeat_required": True,
        "receipt_required": True,
        "canonical_state_write": False,
        "effect_mode": "intent_only",
    }
    if formatted["protocol"] != expected_protocol:
        raise AdapterError("PROTOCOL_DIVERGENCE", projection.adapter_id)
    if formatted["tool_mapping"] != dict(sorted(projection.tool_mapping.items())):
        raise AdapterError("TOOL_MAPPING_DIVERGENCE", projection.adapter_id)
