"""Fail-closed command protocol for Context Vault.

The CLI is deliberately a thin adapter: argparse validates the public protocol,
domain services own policy, and only a small typed effect allow-list may cross the
apply boundary.  Raw request content is never echoed in command output.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import stat
import sys
import uuid
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence, TextIO


MAX_REQUEST_BYTES = 1_000_000
MAX_EFFECT_BYTES = 64 * 1024
ALLOWED_EFFECT_ACTIONS = frozenset({"write_receipt_marker"})
ALLOWED_SCOPE_CAPABILITIES = ALLOWED_EFFECT_ACTIONS
BLOCKED_OUTPUT_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth_token",
        "bearer",
        "client_secret",
        "content",
        "credential",
        "password",
        "private_key",
        "raw_content",
        "raw_transcript",
        "secret",
        "transcript",
    }
)


class CliError(RuntimeError):
    """A safe error whose text may be returned to a caller."""


class BackendUnavailable(CliError):
    pass


class CliRuntime(Protocol):
    """Injection seam used by the real PostgreSQL adapter and isolated tests."""

    def invoke(self, command: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


def _bounded_json(path_text: str) -> dict[str, Any]:
    try:
        data = _read_regular_file(Path(path_text), maximum=MAX_REQUEST_BYTES)
    except (OSError, CliError) as exc:
        raise CliError("request must be a readable bounded regular file") from exc
    try:
        decoded = json.loads(data, parse_constant=_reject_nonfinite_json)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CliError("request is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict) or not all(
        isinstance(key, str) for key in decoded
    ):
        raise CliError("request must be a JSON object")
    return decoded


def _bounded_effect(path_text: str) -> bytes:
    try:
        data = _read_regular_file(Path(path_text), maximum=MAX_EFFECT_BYTES)
    except (OSError, CliError) as exc:
        raise CliError(
            "effect content must be a readable bounded regular file"
        ) from exc
    if not data:
        raise CliError("effect content must be between 1 byte and 64 KiB")
    return data


def _bounded_sha256(path_text: str) -> str:
    try:
        payload = _read_regular_file(Path(path_text), maximum=100 * 1024 * 1024)
    except (OSError, CliError) as exc:
        raise CliError("skill package must be a bounded regular file") from exc
    return hashlib.sha256(payload).hexdigest()


def _read_regular_file(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise CliError("file is not a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, min(64 * 1024, maximum + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise CliError("file exceeds its size limit")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _typed_scope(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) != {
        "paths",
        "capabilities",
    }:
        raise CliError("scope requires exactly paths and capabilities")
    paths = value["paths"]
    capabilities = value["capabilities"]
    if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
        raise CliError("scope paths must be strings")
    if not isinstance(capabilities, list) or not all(
        isinstance(x, str) for x in capabilities
    ):
        raise CliError("scope capabilities must be strings")
    if not paths or not capabilities:
        raise CliError("scope paths and capabilities cannot be empty")
    normalized_paths: list[str] = []
    for raw_path in paths:
        if not raw_path.strip() or raw_path != raw_path.strip():
            raise CliError("scope path must be a non-empty trimmed path")
        parsed = PurePosixPath(raw_path.replace("\\", "/"))
        if parsed.is_absolute() or ".." in parsed.parts:
            raise CliError("scope paths must be safe project-relative paths")
        normalized_paths.append(parsed.as_posix())
    unknown = sorted(set(capabilities) - ALLOWED_SCOPE_CAPABILITIES)
    if unknown:
        raise CliError("scope contains an unsupported capability")
    if len(set(normalized_paths)) != len(normalized_paths) or len(
        set(capabilities)
    ) != len(capabilities):
        raise CliError("scope entries must be unique")
    return {
        "paths": sorted(normalized_paths),
        "capabilities": sorted(capabilities),
    }


def _uuid(value: object, label: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise CliError(f"{label} must be a UUID string")
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CliError(f"{label} must be a UUID") from exc


def _required(payload: Mapping[str, Any], key: str, expected: type[Any]) -> Any:
    value = payload.get(key)
    if not isinstance(value, expected) or (
        isinstance(value, str) and not value.strip()
    ):
        raise CliError(f"{key} is required")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise CliError(f"{label} must be a boolean")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CliError(f"{label} must be an integer")
    return value


def _number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise CliError(f"{label} must be a finite number")
    return float(value)


def _exact_object(
    value: object,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CliError(f"{label} must be an object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise CliError(f"{label} is missing required fields")
    if unknown:
        raise CliError(f"{label} contains unknown fields")
    return value


def _strings(value: object, label: str, *, require_any: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise CliError(f"{label} must contain non-empty strings")
    if require_any and not value:
        raise CliError(f"{label} cannot be empty")
    if len(value) != len(set(value)):
        raise CliError(f"{label} entries must be unique")
    return value


def _object_list(value: object, label: str) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or not value
        or not all(
            isinstance(item, dict)
            and item
            and all(isinstance(key, str) and key.strip() for key in item)
            for item in value
        )
    ):
        raise CliError(f"{label} must contain non-empty JSON objects")
    return value


def _configured_values(name: str, *, separator: str = ",") -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw:
        return ()
    values = tuple(raw.split(separator))
    if any(not value or value != value.strip() for value in values):
        raise CliError(f"{name} contains an invalid configured value")
    if len(values) != len(set(values)):
        raise CliError(f"{name} contains duplicate configured values")
    return values


def _instant(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise CliError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CliError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CliError(f"{label} must include a timezone")
    return parsed


def _knowledge_actor(value: object, label: str) -> Any:
    from src.context_vault.knowledge import Actor, ActorKind

    raw = _exact_object(
        value,
        required=frozenset({"kind", "actor_id"}),
        label=label,
    )
    try:
        return Actor(
            ActorKind(_required(raw, "kind", str).upper()),
            _required(raw, "actor_id", str),
        )
    except ValueError as exc:
        raise CliError(f"{label}.kind is invalid") from exc


def _public(value: Any) -> Any:
    """Convert domain values to deterministic JSON without raw content fields."""

    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return {
            str(key): _public(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key).casefold().replace("-", "_")
            not in BLOCKED_OUTPUT_KEYS | {"core_text", "payload_json"}
        }
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_public(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (uuid.UUID, Path)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return type(value).__name__


class DefaultRuntime:
    """Local domain adapter; DB-backed work operations are opened lazily."""

    def __init__(self) -> None:
        self._db: Any | None = None

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def invoke(self, command: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if command == "doctor":
            return self._doctor(payload)
        if command == "project.register":
            return self._project_register(payload)
        if command == "context.compile":
            return self._context_compile(payload)
        if command.startswith("work."):
            return self._work(command, payload)
        if command.startswith("knowledge."):
            return self._knowledge(command, payload)
        if command == "provider.test":
            return self._provider_test(payload)
        if command == "skill.verify":
            return self._skill_verify(payload)
        raise CliError("unknown command")

    @staticmethod
    def _doctor(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        from src.context_vault.project_manifest import discover_project

        recognized = discover_project(Path(str(payload["start"])))
        return {
            "database_configured": bool(os.environ.get("DATABASE_URL")),
            "manifest_hash": recognized.manifest.manifest_hash,
            "manifest_path": str(recognized.manifest_path),
            "project_id": str(recognized.manifest.project_id),
            "project_root": str(recognized.root),
            "status": "ready" if os.environ.get("DATABASE_URL") else "degraded",
        }

    def _project_register(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        from src.context_vault.project_manifest import discover_project

        manifest_path = Path(str(payload["manifest"]))
        recognized = discover_project(manifest_path.parent.parent)
        try:
            is_exact = recognized.manifest_path.samefile(manifest_path)
        except OSError as exc:
            raise CliError("manifest path cannot be resolved") from exc
        if not is_exact:
            raise CliError("manifest is not the recognized project manifest")
        return self._registry_service().register_project(recognized)

    def _context_compile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        from src.context_vault.adapters import (
            AdapterHub,
            CoreContextEnvelope,
            WorkAuthority,
        )
        from src.context_vault.context_compiler import (
            ContextCompiler,
            ContextItem,
            ContextRole,
            DataClassification,
            ItemState,
            LoadTier,
            ProviderContextPolicy,
        )

        request = _exact_object(
            payload.get("request"),
            required=frozenset(
                {
                    "attempt_id",
                    "context_window",
                    "reserved_output",
                    "safety_margin",
                    "provider_policy",
                    "items",
                }
            ),
            optional=frozenset(
                {
                    "relevant_source_ids",
                    "retrieved_source_ids",
                    "preload_all",
                    "binding",
                }
            ),
            label="context request",
        )
        raw_items = request.get("items")
        if not isinstance(raw_items, list):
            raise CliError("context items must be a list")
        parsed_items = tuple(
            _exact_object(
                item,
                required=frozenset(
                    {
                        "source_id",
                        "logical_id",
                        "version",
                        "content",
                        "reason",
                        "token_cost",
                        "load_tier",
                        "classification",
                    }
                ),
                optional=frozenset(
                    {"role", "state", "relevance_score", "supersedes", "local_only"}
                ),
                label="context item",
            )
            for item in raw_items
        )
        items = tuple(
            ContextItem.from_content(
                source_id=_required(item, "source_id", str),
                logical_id=_required(item, "logical_id", str),
                version=_integer(item.get("version"), "version"),
                content=_required(item, "content", str),
                reason=_required(item, "reason", str),
                token_cost=_integer(item.get("token_cost"), "token_cost"),
                load_tier=LoadTier(_required(item, "load_tier", str)),
                classification=DataClassification(
                    _required(item, "classification", str)
                ),
                role=ContextRole(str(item.get("role", "KNOWLEDGE"))),
                state=ItemState(str(item.get("state", "ACTIVE"))),
                relevance_score=_number(
                    item.get("relevance_score", 0.0), "relevance_score"
                ),
                supersedes=tuple(_strings(item.get("supersedes", []), "supersedes")),
                local_only=_boolean(item.get("local_only", False), "local_only"),
            )
            for item in parsed_items
        )
        policy_raw = _exact_object(
            request.get("provider_policy"),
            required=frozenset({"provider_id", "is_remote", "allowed_classifications"}),
            label="provider_policy",
        )
        classifications = policy_raw.get("allowed_classifications")
        if not isinstance(classifications, list):
            raise CliError("allowed_classifications must be a list")
        compiled = ContextCompiler().compile(
            attempt_id=_required(request, "attempt_id", str),
            items=items,
            provider_policy=ProviderContextPolicy(
                provider_id=_required(policy_raw, "provider_id", str),
                is_remote=_boolean(policy_raw.get("is_remote", False), "is_remote"),
                allowed_classifications=frozenset(
                    DataClassification(value) for value in classifications
                ),
            ),
            context_window=_integer(request.get("context_window"), "context_window"),
            reserved_output=_integer(request.get("reserved_output"), "reserved_output"),
            safety_margin=_integer(request.get("safety_margin"), "safety_margin"),
            relevant_source_ids=frozenset(
                _strings(request.get("relevant_source_ids", []), "relevant_source_ids")
            ),
            retrieved_source_ids=frozenset(
                _strings(
                    request.get("retrieved_source_ids", []), "retrieved_source_ids"
                )
            ),
            preload_all=_boolean(request.get("preload_all", False), "preload_all"),
        )
        result: dict[str, Any] = {
            "attempt_id": compiled.attempt_id,
            "available_input_tokens": compiled.available_input_tokens,
            "excluded": compiled.excluded,
            "included": [
                {
                    "classification": item.classification,
                    "content_hash": item.content_hash,
                    "load_tier": item.load_tier,
                    "logical_id": item.logical_id,
                    "reason": item.reason,
                    "source_id": item.source_id,
                    "token_cost": item.token_cost,
                    "version": item.version,
                }
                for item in compiled.items
            ],
            "manifest_hash": compiled.manifest_hash,
            "provider_id": compiled.provider_id,
            "token_cost": compiled.token_cost,
        }
        binding_value = request.get("binding")
        if binding_value is None:
            return result
        binding = _exact_object(
            binding_value,
            required=frozenset(
                {"model_id", "work_authority", "adapter_ids", "tool_mapping"}
            ),
            label="context binding",
        )
        authority_raw = _exact_object(
            binding["work_authority"],
            required=frozenset(
                {
                    "work_item_id",
                    "attempt_id",
                    "claim_id",
                    "fencing_token",
                    "current_revision",
                }
            ),
            label="work_authority",
        )
        authority = WorkAuthority(
            work_item_id=_required(authority_raw, "work_item_id", str),
            attempt_id=_required(authority_raw, "attempt_id", str),
            claim_id=_required(authority_raw, "claim_id", str),
            fencing_token=_integer(authority_raw.get("fencing_token"), "fencing_token"),
            current_revision=_required(authority_raw, "current_revision", str),
        )
        if authority.attempt_id != compiled.attempt_id:
            raise CliError("context binding attempt_id differs from compile request")
        tool_mapping_raw = binding["tool_mapping"]
        if not isinstance(tool_mapping_raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in tool_mapping_raw.items()
        ):
            raise CliError("tool_mapping must map strings to strings")
        adapter_ids = _strings(binding["adapter_ids"], "adapter_ids", require_any=True)
        model_id = _required(binding, "model_id", str)
        hub = AdapterHub.default()
        core = CoreContextEnvelope.from_compiled_context(compiled)
        projections = tuple(
            hub.project(
                adapter_id,
                core,
                authority,
                model_id=model_id,
                tool_mapping=tool_mapping_raw,
            )
            for adapter_id in adapter_ids
        )
        bound = self._registry_service().bind_compiled_context(
            compiled=compiled,
            projections=projections,
            model_id=model_id,
            context_window=_integer(request["context_window"], "context_window"),
            reserved_output=_integer(request["reserved_output"], "reserved_output"),
            safety_margin=_integer(request["safety_margin"], "safety_margin"),
        )
        result["adapter_hashes"] = bound["adapter_hashes"]
        result["persisted"] = True
        return result

    def _service(self) -> Any:
        if self._db is None:
            try:
                from src.db import SessionLocal
            except Exception as exc:
                raise BackendUnavailable(
                    "work command requires valid backend configuration"
                ) from exc
            self._db = SessionLocal()
        from src.application.work_graph import WorkGraphService

        return WorkGraphService(self._db)

    def _registry_service(self) -> Any:
        if self._db is None:
            try:
                from src.db import SessionLocal
            except Exception as exc:
                raise BackendUnavailable(
                    "command requires valid backend configuration"
                ) from exc
            self._db = SessionLocal()
        from src.application.context_vault_registry import (
            ContextVaultRegistryService,
        )

        authenticated_raw = os.environ.get("CV_AUTHENTICATED_PRINCIPAL_ID")
        authenticated = (
            None
            if authenticated_raw is None
            else _uuid(authenticated_raw, "CV_AUTHENTICATED_PRINCIPAL_ID")
        )
        return ContextVaultRegistryService(
            self._db,
            authenticated_principal_id=authenticated,
            trusted_policy_ids=frozenset(_configured_values("CV_TRUSTED_POLICY_IDS")),
        )

    def _effect_service(self, effect_root: str) -> Any:
        from src.application.work_graph import (
            ScopedMutationDispatcher,
            WorkGraphService,
        )

        self._service()  # lazily open the canonical database session
        return WorkGraphService(
            self._db,
            dispatcher=ScopedMutationDispatcher(Path(effect_root)),
        )

    def _knowledge(self, command: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = _exact_object(
            payload.get("request"),
            required=(
                frozenset(
                    {
                        "workspace_id",
                        "stable_key",
                        "content",
                        "source_ids",
                        "evidence_ids",
                        "owner_id",
                        "scope",
                        "confidence",
                        "classification",
                        "proposed_by",
                        "valid_from",
                    }
                )
                if command == "knowledge.propose"
                else frozenset({"revision_id", "actor"})
                if command == "knowledge.review"
                else frozenset({"revision_id", "actor"})
            ),
            optional=(
                frozenset({"project_id", "valid_until", "supersedes_revision_id"})
                if command == "knowledge.propose"
                else frozenset()
            ),
            label="knowledge request",
        )
        service = self._registry_service()
        if command == "knowledge.propose":
            confidence = _number(request["confidence"], "confidence")
            return service.propose_knowledge(
                workspace_id=_uuid(request["workspace_id"], "workspace_id"),
                project_id=(
                    None
                    if request.get("project_id") is None
                    else _uuid(request["project_id"], "project_id")
                ),
                stable_key=_required(request, "stable_key", str),
                content=_required(request, "content", str),
                source_ids=_strings(
                    request["source_ids"], "source_ids", require_any=True
                ),
                evidence_ids=_strings(
                    request["evidence_ids"], "evidence_ids", require_any=True
                ),
                owner_id=_uuid(request["owner_id"], "owner_id"),
                scope=_required(request, "scope", str),
                confidence=confidence,
                classification=_required(request, "classification", str),
                proposed_by=_knowledge_actor(request["proposed_by"], "proposed_by"),
                valid_from=_instant(request["valid_from"], "valid_from"),
                valid_until=(
                    None
                    if request.get("valid_until") is None
                    else _instant(request["valid_until"], "valid_until")
                ),
                supersedes_revision_id=(
                    None
                    if request.get("supersedes_revision_id") is None
                    else _uuid(
                        request["supersedes_revision_id"],
                        "supersedes_revision_id",
                    )
                ),
            )
        actor = _knowledge_actor(request["actor"], "actor")
        revision_id = _uuid(request["revision_id"], "revision_id")
        if command == "knowledge.review":
            return service.review_knowledge(revision_id, reviewer=actor)
        return service.approve_knowledge(
            revision_id,
            approver=actor,
        )

    def _provider_test(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Validate and persist registry metadata without invoking a provider."""

        from src.context_vault.registry import (
            Benchmark,
            Capability,
            DataClassification,
            DistanceMetric,
            EmbeddingProfile,
            HealthState,
            ModelRecord,
            ProviderRecord,
        )

        request = _exact_object(
            payload.get("request"),
            required=frozenset({"provider"}),
            optional=frozenset({"model"}),
            label="provider request",
        )
        provider_raw = _exact_object(
            request["provider"],
            required=frozenset(
                {
                    "provider_id",
                    "adapter_id",
                    "locality",
                    "network_required",
                    "health",
                    "circuit_open",
                    "version",
                    "config_hash",
                    "metadata",
                }
            ),
            optional=frozenset({"secret_ref"}),
            label="provider",
        )
        try:
            provider = ProviderRecord(
                provider_id=_required(provider_raw, "provider_id", str),
                adapter_id=_required(provider_raw, "adapter_id", str),
                locality=_required(provider_raw, "locality", str),
                network_required=_required(provider_raw, "network_required", bool),
                health=HealthState(_required(provider_raw, "health", str)),
                circuit_open=_required(provider_raw, "circuit_open", bool),
                version=_required(provider_raw, "version", str),
                config_hash=_required(provider_raw, "config_hash", str),
                secret_ref=provider_raw.get("secret_ref"),
                metadata=_required(provider_raw, "metadata", dict),
            )
        except ValueError as exc:
            raise CliError("provider registry metadata is invalid") from exc
        model_raw_value = request.get("model")
        if model_raw_value is None:
            provider_result = self._registry_service().register_provider(provider)
            return {**provider_result, "provider_call_performed": False}
        model_raw = _exact_object(
            model_raw_value,
            required=frozenset(
                {
                    "model_id",
                    "provider_id",
                    "capabilities",
                    "context_limit",
                    "output_limit",
                    "allowed_data",
                    "benchmark",
                    "version",
                    "config_hash",
                }
            ),
            optional=frozenset(
                {
                    "health",
                    "circuit_open",
                    "accessible",
                    "embedding_profile",
                    "fallback_model_ids",
                }
            ),
            label="model",
        )
        benchmark_raw = _exact_object(
            model_raw["benchmark"],
            required=frozenset(
                {"latency_p95_ms", "cost_micro_usd", "quality_score", "sample_size"}
            ),
            label="benchmark",
        )
        profile_raw_value = model_raw.get("embedding_profile")
        profile = None
        if profile_raw_value is not None:
            profile_raw = _exact_object(
                profile_raw_value,
                required=frozenset(
                    {
                        "profile_id",
                        "dimension",
                        "distance",
                        "query_prefix",
                        "document_prefix",
                        "config_hash",
                    }
                ),
                label="embedding_profile",
            )
            profile = EmbeddingProfile(
                profile_id=_required(profile_raw, "profile_id", str),
                dimension=_integer(profile_raw.get("dimension"), "dimension"),
                distance=DistanceMetric(_required(profile_raw, "distance", str)),
                query_prefix=_required(profile_raw, "query_prefix", str),
                document_prefix=_required(profile_raw, "document_prefix", str),
                config_hash=_required(profile_raw, "config_hash", str),
            )
        try:
            model = ModelRecord(
                model_id=_required(model_raw, "model_id", str),
                provider_id=_required(model_raw, "provider_id", str),
                capabilities=frozenset(
                    Capability(value)
                    for value in _strings(
                        model_raw["capabilities"], "capabilities", require_any=True
                    )
                ),
                context_limit=_integer(model_raw.get("context_limit"), "context_limit"),
                output_limit=_integer(model_raw.get("output_limit"), "output_limit"),
                allowed_data=frozenset(
                    DataClassification(value)
                    for value in _strings(
                        model_raw["allowed_data"], "allowed_data", require_any=True
                    )
                ),
                benchmark=Benchmark(
                    latency_p95_ms=benchmark_raw["latency_p95_ms"],
                    cost_micro_usd=benchmark_raw["cost_micro_usd"],
                    quality_score=benchmark_raw["quality_score"],
                    sample_size=benchmark_raw["sample_size"],
                ),
                version=_required(model_raw, "version", str),
                config_hash=_required(model_raw, "config_hash", str),
                health=HealthState(str(model_raw.get("health", "healthy"))),
                circuit_open=_boolean(
                    model_raw.get("circuit_open", False), "circuit_open"
                ),
                accessible=_boolean(model_raw.get("accessible", True), "accessible"),
                embedding_profile=profile,
                fallback_model_ids=tuple(
                    _strings(
                        model_raw.get("fallback_model_ids", []), "fallback_model_ids"
                    )
                ),
            )
        except (TypeError, ValueError) as exc:
            raise CliError("model registry metadata is invalid") from exc
        provider_result, model_result = (
            self._registry_service().register_provider_model(provider, model)
        )
        return {
            "model": model_result,
            "provider": provider_result,
            "provider_call_performed": False,
        }

    def _skill_verify(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        from src.context_vault.registry import (
            SkillAdmissionPolicy,
            TrustLevel,
        )

        request = _exact_object(
            payload.get("request"),
            required=frozenset({"skill_id", "package_path", "adapter_id"}),
            optional=frozenset({"requested_permissions"}),
            label="skill request",
        )
        allow_network_raw = os.environ.get("CV_SKILL_ALLOW_NETWORK", "false")
        if allow_network_raw not in {"true", "false"}:
            raise CliError("CV_SKILL_ALLOW_NETWORK must be true or false")
        policy = SkillAdmissionPolicy(
            allowed_trust_levels=frozenset({TrustLevel.VERIFIED}),
            allowed_tools=frozenset(_configured_values("CV_SKILL_ALLOWED_TOOLS")),
            allowed_permissions=frozenset(
                _configured_values("CV_SKILL_ALLOWED_PERMISSIONS")
            ),
            allow_network=allow_network_raw == "true",
            filesystem_roots=tuple(
                _configured_values("CV_SKILL_FILESYSTEM_ROOTS", separator=os.pathsep)
            ),
        )
        projection = self._registry_service().verify_registered_skill(
            _required(request, "skill_id", str),
            observed_package_hash=_bounded_sha256(
                _required(request, "package_path", str)
            ),
            adapter_id=_required(request, "adapter_id", str),
            policy=policy,
            requested_permissions=frozenset(
                _strings(
                    request.get("requested_permissions", []), "requested_permissions"
                )
            ),
        )
        return _public(projection)

    def _work(self, command: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        from src.application.work_graph import PreparedWork

        if command == "work.create":
            request = _exact_object(
                payload.get("request"),
                required=frozenset(
                    {
                        "workspace_id",
                        "title",
                        "objective",
                        "scope",
                        "expected_revision",
                        "created_by_principal_id",
                        "acceptance_criteria",
                        "evidence_requirements",
                    }
                ),
                optional=frozenset(
                    {
                        "project_id",
                        "exclusions",
                        "required_approvals",
                        "risk_class",
                        "priority",
                        "owner_principal_id",
                        "parent_id",
                    }
                ),
                label="work create request",
            )
            risk_class = request.get("risk_class", "low")
            if not isinstance(risk_class, str) or risk_class not in {
                "low",
                "medium",
                "high",
                "critical",
            }:
                raise CliError("risk_class is invalid")
            workspace_id = _uuid(request.get("workspace_id"), "workspace_id")
            project_id = (
                None
                if request.get("project_id") is None
                else _uuid(request["project_id"], "project_id")
            )
            title = _required(request, "title", str)
            objective = _required(request, "objective", str)
            scope = _typed_scope(request.get("scope"))
            expected_revision = _required(request, "expected_revision", str)
            created_by_principal_id = _uuid(
                request.get("created_by_principal_id"),
                "created_by_principal_id",
            )
            acceptance_criteria = _object_list(
                request.get("acceptance_criteria"), "acceptance_criteria"
            )
            evidence_requirements = _strings(
                request.get("evidence_requirements"),
                "evidence_requirements",
                require_any=True,
            )
            exclusions = _strings(request.get("exclusions", []), "exclusions")
            required_approvals = _strings(
                request.get("required_approvals", []), "required_approvals"
            )
            priority = _integer(request.get("priority", 0), "priority")
            owner_principal_id = (
                None
                if request.get("owner_principal_id") is None
                else _uuid(request["owner_principal_id"], "owner_principal_id")
            )
            parent_id = (
                None
                if request.get("parent_id") is None
                else _uuid(request["parent_id"], "parent_id")
            )
            service = self._service()
            item = service.create_work_item(
                workspace_id=workspace_id,
                project_id=project_id,
                title=title,
                objective=objective,
                scope=scope,
                expected_revision=expected_revision,
                created_by_principal_id=created_by_principal_id,
                acceptance_criteria=acceptance_criteria,
                evidence_requirements=evidence_requirements,
                exclusions=exclusions,
                required_approvals=required_approvals,
                risk_class=risk_class,
                priority=priority,
                owner_principal_id=owner_principal_id,
                parent_id=parent_id,
            )
            if not payload.get("draft"):
                item = service.mark_ready(item.id, actor_id="cv-cli")
            return {
                "revision": item.revision,
                "status": item.status,
                "work_item_id": item.id,
            }
        work_id = _uuid(payload.get("work_item_id"), "work_item_id")
        if command == "work.prepare":
            service = self._service()
            return _public(
                service.prepare(
                    work_id,
                    observed_revision=_required(payload, "observed_revision", str),
                )
            )
        if command == "work.claim":
            service = self._service()
            prepared_raw = payload["prepared"]
            prepared = PreparedWork(
                work_item_id=_uuid(prepared_raw.get("work_item_id"), "work_item_id"),
                work_item_revision=int(prepared_raw["work_item_revision"]),
                expected_revision=str(prepared_raw["expected_revision"]),
                drift_token=str(prepared_raw["drift_token"]),
                scope_hash=str(prepared_raw["scope_hash"]),
            )
            return _public(
                service.claim(
                    work_id,
                    prepared=prepared,
                    claimant_id=str(payload["claimant_id"]),
                    executor_id=str(payload["executor_id"]),
                    resource_scope=_typed_scope(payload["scope"]),
                    lease_duration=timedelta(seconds=int(payload["lease_seconds"])),
                    idempotency_key=str(payload["idempotency_key"]),
                    adapter_id=payload.get("adapter_id"),
                    model_id=payload.get("model_id"),
                    provider_id=payload.get("provider_id"),
                    input_context_manifest_hash=payload.get("context_manifest_hash"),
                )
            )
        if command == "work.heartbeat":
            service = self._service()
            return {
                "claim_id": payload["claim_id"],
                "expires_at": service.heartbeat(
                    _uuid(payload["claim_id"], "claim_id"),
                    fencing_token=int(payload["fencing_token"]),
                    extend_by=timedelta(seconds=int(payload["extend_seconds"])),
                ),
            }
        if command == "work.apply":
            from src.application.work_graph import (
                EffectCapability,
                EffectRequest,
            )

            scope = _typed_scope(payload["scope"])
            action = str(payload["action"])
            if action not in ALLOWED_EFFECT_ACTIONS:
                raise CliError("effect action is not allow-listed")
            relative_path = str(payload["relative_path"])
            if (
                relative_path not in scope["paths"]
                or action not in scope["capabilities"]
            ):
                raise CliError("effect is outside the declared scope")
            effect_request = EffectRequest(
                capability=EffectCapability(action),
                relative_path=relative_path,
                content=_bounded_effect(str(payload["content_file"])),
            )
            effect_hash = hashlib.sha256(
                json.dumps(
                    {
                        "effect": effect_request.digest_payload(),
                        "scope": scope,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if payload.get("dry_run"):
                return {
                    "action": action,
                    "dry_run": True,
                    "effect_applied": False,
                    "effect_hash": effect_hash,
                    "scope": scope,
                }

            effect_root = payload.get("effect_root") or os.environ.get("CV_EFFECT_ROOT")
            if not isinstance(effect_root, str) or not effect_root.strip():
                raise BackendUnavailable(
                    "apply requires --effect-root or CV_EFFECT_ROOT"
                )
            service = self._effect_service(effect_root)

            return _public(
                service.apply(
                    work_id,
                    claim_id=_uuid(payload["claim_id"], "claim_id"),
                    fencing_token=int(payload["fencing_token"]),
                    observed_revision=str(payload["expected_revision"]),
                    idempotency_key=str(payload["idempotency_key"]),
                    command="cv work apply",
                    tool="context-vault",
                    action=action,
                    signer_type="cli",
                    signer_id=str(payload["signer_id"]),
                    effect_request=effect_request,
                )
            )
        if command == "work.verify":
            service = self._service()
            return _public(
                service.verify(
                    work_id,
                    attempt_id=_uuid(payload["attempt_id"], "attempt_id"),
                    idempotency_key=str(payload["idempotency_key"]),
                    acceptance_evidence=list(payload["acceptance_evidence"]),
                    test_evidence_refs=list(payload["test_evidence_refs"]),
                    signer_type="cli",
                    signer_id=str(payload["signer_id"]),
                    observed_revision=str(payload["observed_revision"]),
                )
            )
        if command == "work.close":
            service = self._service()
            return _public(
                service.close(
                    work_id,
                    attempt_id=_uuid(payload["attempt_id"], "attempt_id"),
                    verify_receipt_id=_uuid(
                        payload["verify_receipt_id"], "verify_receipt_id"
                    ),
                    idempotency_key=str(payload["idempotency_key"]),
                    signer_type="cli",
                    signer_id=str(payload["signer_id"]),
                    after_revision=str(payload["after_revision"]),
                )
            )
        if command == "work.reconcile":
            service = self._service()
            return {
                "claim_ids": service.reconcile_stale_leases(limit=int(payload["limit"]))
            }
        raise CliError("unknown work command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cv")
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    root = parser.add_subparsers(dest="group", required=True)

    doctor = root.add_parser("doctor")
    doctor.add_argument("--start", default=".")

    project = root.add_parser("project").add_subparsers(dest="operation", required=True)
    register = project.add_parser("register")
    register.add_argument("--manifest", default=".contextvault/project.yaml")

    work = root.add_parser("work").add_subparsers(dest="operation", required=True)
    create = work.add_parser("create")
    create.add_argument("--request", required=True)
    create.add_argument("--draft", action="store_true")
    prepare = work.add_parser("prepare")
    prepare.add_argument("work_item_id")
    prepare.add_argument("--observed-revision", required=True)
    claim = work.add_parser("claim")
    claim.add_argument("work_item_id")
    claim.add_argument("--prepared", required=True)
    claim.add_argument("--scope", required=True)
    claim.add_argument("--claimant-id", required=True)
    claim.add_argument("--executor-id", required=True)
    claim.add_argument("--lease-seconds", type=int, default=120)
    claim.add_argument("--idempotency-key", required=True)
    claim.add_argument("--adapter-id")
    claim.add_argument("--model-id")
    claim.add_argument("--provider-id")
    claim.add_argument("--context-manifest-hash")
    heartbeat = work.add_parser("heartbeat")
    heartbeat.add_argument("claim_id")
    heartbeat.add_argument("--fencing-token", type=int, required=True)
    heartbeat.add_argument("--extend-seconds", type=int, default=120)
    apply = work.add_parser("apply")
    apply.add_argument("work_item_id")
    apply.add_argument("--claim", required=True)
    apply.add_argument("--fencing-token", type=int, required=True)
    apply.add_argument("--expected-revision", required=True)
    apply.add_argument("--idempotency-key", required=True)
    apply.add_argument("--scope", required=True)
    apply.add_argument(
        "--action",
        dest="effect_action",
        choices=sorted(ALLOWED_EFFECT_ACTIONS),
        required=True,
    )
    apply.add_argument("--relative-path", required=True)
    apply.add_argument("--content-file", required=True)
    apply.add_argument("--effect-root")
    apply.add_argument("--signer-id", required=True)
    apply.add_argument("--dry-run", action="store_true")
    verify = work.add_parser("verify")
    verify.add_argument("work_item_id")
    verify.add_argument("--attempt", required=True)
    verify.add_argument("--idempotency-key", required=True)
    verify.add_argument("--acceptance-evidence", action="append", required=True)
    verify.add_argument("--test-evidence", action="append", required=True)
    verify.add_argument("--observed-revision", required=True)
    verify.add_argument("--signer-id", required=True)
    close = work.add_parser("close")
    close.add_argument("work_item_id")
    close.add_argument("--attempt", required=True)
    close.add_argument("--verify-receipt", required=True)
    close.add_argument("--idempotency-key", required=True)
    close.add_argument("--after-revision", required=True)
    close.add_argument("--signer-id", required=True)
    reconcile = work.add_parser("reconcile")
    reconcile.add_argument("--limit", type=int, default=100)

    context = root.add_parser("context").add_subparsers(dest="operation", required=True)
    context_compile = context.add_parser("compile")
    context_compile.add_argument("--request", required=True)

    knowledge = root.add_parser("knowledge").add_subparsers(
        dest="operation", required=True
    )
    for action in ("propose", "review", "approve"):
        knowledge.add_parser(action).add_argument("--request", required=True)

    provider = root.add_parser("provider").add_subparsers(
        dest="operation", required=True
    )
    provider.add_parser("test").add_argument("--request", required=True)
    skill = root.add_parser("skill").add_subparsers(dest="operation", required=True)
    skill.add_parser("verify").add_argument("--request", required=True)
    return parser


def _dispatch(
    args: argparse.Namespace, runtime: CliRuntime
) -> tuple[str, Mapping[str, Any]]:
    group = str(args.group)
    operation = getattr(args, "operation", None)
    command = group if operation is None else f"{group}.{operation}"
    payload: dict[str, Any] = {
        key: value
        for key, value in vars(args).items()
        if key not in {"json", "group", "operation"}
    }
    for key in ("request", "prepared", "scope"):
        if key in payload:
            payload[key] = _bounded_json(payload[key])
    if command in {"work.claim", "work.apply"}:
        payload["scope"] = _typed_scope(payload["scope"])
    if "claim" in payload:
        payload["claim_id"] = payload.pop("claim")
    if "effect_action" in payload:
        payload["action"] = payload.pop("effect_action")
    if "attempt" in payload:
        payload["attempt_id"] = payload.pop("attempt")
    if "verify_receipt" in payload:
        payload["verify_receipt_id"] = payload.pop("verify_receipt")
    return command, runtime.invoke(command, payload)


def _render_human(command: str, result: Mapping[str, Any]) -> str:
    public = _public(result)
    fields = " ".join(
        f"{key}={json.dumps(value, sort_keys=True, ensure_ascii=False)}"
        for key, value in public.items()
    )
    return f"OK {command}" + (f" {fields}" if fields else "")


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime: CliRuntime | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    raw_args = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in raw_args
    if json_mode:
        raw_args.remove("--json")
        raw_args.insert(0, "--json")
    selected_runtime = runtime or DefaultRuntime()
    command = "unknown"
    try:
        args = build_parser().parse_args(raw_args)
        command, result = _dispatch(args, selected_runtime)
        envelope = {"command": command, "ok": True, "result": _public(result)}
        if args.json:
            print(
                json.dumps(
                    envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
                file=output,
            )
        else:
            print(_render_human(command, result), file=output)
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        safe_detail = str(exc) if isinstance(exc, CliError) else "command failed"
        envelope = {
            "command": command,
            "error": {"code": type(exc).__name__, "detail": safe_detail},
            "ok": False,
        }
        if json_mode:
            print(
                json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                file=output,
            )
        else:
            print(f"ERROR {command} {type(exc).__name__}: {safe_detail}", file=errors)
        return 2
    finally:
        selected_runtime.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
