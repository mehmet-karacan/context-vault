"""Fail-closed provider, model, and skill registries.

The records in this module deliberately contain references to secrets and receipts,
never secret values or mutable execution evidence.  They are pure domain values so
the PostgreSQL repository can remain the canonical authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit


_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}")
_SECRET_REF = re.compile(r"secret://[A-Za-z0-9][A-Za-z0-9._/-]{0,254}")
_RECEIPT_REF = re.compile(r"receipt://[A-Za-z0-9][A-Za-z0-9._/-]{0,254}")
_SECRET_KEYS = (
    "api_key",
    "access_token",
    "auth_token",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_MUTABLE_REFS = frozenset({"head", "latest", "main", "master", "stable"})
_AUTHORITY_PERMISSIONS = frozenset(
    {
        "change_root_authority",
        "modify_security_policy",
        "write_canonical_state",
    }
)


class RegistryError(ValueError):
    """A stable, machine-readable registry admission failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class Capability(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    OCR = "ocr"
    VISION = "vision"
    TOOL_USE = "tool_use"


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class DistanceMetric(StrEnum):
    COSINE = "cosine"
    INNER_PRODUCT = "inner_product"
    L2 = "l2"


class TrustLevel(StrEnum):
    VERIFIED = "verified"
    RESTRICTED = "restricted"
    UNTRUSTED = "untrusted"


class SkillScope(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"
    WORK_ITEM = "work_item"


def canonical_hash(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RegistryError("NON_CANONICAL_JSON", str(exc)) from exc
    return hashlib.sha256(encoded).hexdigest()


def _require_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or "*" in value:
        raise RegistryError("INVALID_IDENTIFIER", f"{field_name} is not exact")


def _require_sha256(value: str, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise RegistryError("INVALID_HASH", f"{field_name} must be lowercase sha256")


def _require_receipt_ref(value: str, field_name: str) -> None:
    if not _RECEIPT_REF.fullmatch(value):
        raise RegistryError("INVALID_RECEIPT_REF", f"{field_name} must be receipt://")


def _assert_secretless(value: Any, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(marker in normalized for marker in _SECRET_KEYS):
                raise RegistryError(
                    "SECRET_IN_METADATA", f"forbidden key at {path}.{key}"
                )
            _assert_secretless(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, nested in enumerate(value):
            _assert_secretless(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lstrip().casefold()
        if lowered.startswith(
            (
                "basic ",
                "bearer ",
                "gho_",
                "ghp_",
                "ghr_",
                "ghs_",
                "ghu_",
                "github_pat_",
                "sk-",
                "-----begin private key",
            )
        ):
            raise RegistryError("SECRET_IN_METADATA", f"secret-shaped value at {path}")
        if _url_contains_credentials(value):
            raise RegistryError("SECRET_IN_METADATA", f"credential URL at {path}")
    elif isinstance(value, float) and not math.isfinite(value):
        raise RegistryError("NON_CANONICAL_JSON", f"non-finite number at {path}")


def _freeze_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise RegistryError("INVALID_METADATA", "metadata keys must be strings")
        return MappingProxyType(
            {key: _freeze_metadata(nested) for key, nested in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_metadata(nested) for nested in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_metadata(nested) for nested in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise RegistryError("INVALID_METADATA", f"unsupported value {type(value).__name__}")


def _normalized_absolute_scope(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise RegistryError("INVALID_FILESYSTEM_SCOPE", "scope must be a POSIX path")
    if value != "/" and any(part in {"", ".", ".."} for part in value.split("/")[1:]):
        raise RegistryError("INVALID_FILESYSTEM_SCOPE", value)
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value:
        raise RegistryError("INVALID_FILESYSTEM_SCOPE", value)
    return value


def _tuple_value(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)):
        raise RegistryError("INVALID_COLLECTION", f"{field_name} must be a collection")
    try:
        return tuple(value)
    except TypeError as exc:
        raise RegistryError(
            "INVALID_COLLECTION", f"{field_name} must be iterable"
        ) from exc


def _url_contains_credentials(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return True
    if parsed.username is not None or parsed.password is not None:
        return True
    return any(
        any(marker in key.casefold().replace("-", "_") for marker in _SECRET_KEYS)
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    )


def _clean_text(value: Any, field_name: str, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RegistryError(code, f"{field_name} must be nonempty and normalized")
    return value


def _exact_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _exact_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class Benchmark:
    latency_p95_ms: float
    cost_micro_usd: int
    quality_score: float
    sample_size: int

    def __post_init__(self) -> None:
        if (
            not _exact_finite_number(self.latency_p95_ms)
            or self.latency_p95_ms < 0
            or not _exact_int(self.cost_micro_usd)
            or self.cost_micro_usd < 0
        ):
            raise RegistryError(
                "INVALID_BENCHMARK", "latency and cost cannot be negative"
            )
        if (
            not _exact_finite_number(self.quality_score)
            or not 0 <= self.quality_score <= 1
            or not _exact_int(self.sample_size)
            or self.sample_size <= 0
        ):
            raise RegistryError("INVALID_BENCHMARK", "quality/sample size is invalid")


@dataclass(frozen=True)
class EmbeddingProfile:
    profile_id: str
    dimension: int
    distance: DistanceMetric
    query_prefix: str
    document_prefix: str
    config_hash: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "distance", DistanceMetric(self.distance))
        except (TypeError, ValueError) as exc:
            raise RegistryError(
                "INVALID_EMBEDDING_PROFILE", "distance is invalid"
            ) from exc
        _require_identifier(self.profile_id, "profile_id")
        _require_sha256(self.config_hash, "embedding config_hash")
        if not isinstance(self.query_prefix, str) or not isinstance(
            self.document_prefix, str
        ):
            raise RegistryError("INVALID_EMBEDDING_PROFILE", "prefixes must be text")
        if not _exact_int(self.dimension) or self.dimension <= 0:
            raise RegistryError(
                "INVALID_EMBEDDING_PROFILE", "dimension must be positive"
            )

    @property
    def compatibility_key(self) -> tuple[Any, ...]:
        return (
            self.profile_id,
            self.dimension,
            self.distance,
            self.query_prefix,
            self.document_prefix,
            self.config_hash,
        )


@dataclass(frozen=True)
class ProviderRecord:
    provider_id: str
    adapter_id: str
    locality: str
    network_required: bool
    health: HealthState
    circuit_open: bool
    version: str
    config_hash: str
    secret_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "health", HealthState(self.health))
        except (TypeError, ValueError) as exc:
            raise RegistryError("INVALID_HEALTH_STATE", str(self.health)) from exc
        if not isinstance(self.network_required, bool) or not isinstance(
            self.circuit_open, bool
        ):
            raise RegistryError("INVALID_PROVIDER_POLICY", "booleans must be exact")
        _require_identifier(self.provider_id, "provider_id")
        _require_identifier(self.adapter_id, "adapter_id")
        _require_sha256(self.config_hash, "provider config_hash")
        _clean_text(self.version, "version", "PROVIDER_VERSION_NOT_EXACT")
        if self.version.casefold() in _MUTABLE_REFS:
            raise RegistryError("PROVIDER_VERSION_NOT_EXACT", self.version)
        if self.locality not in {"local", "remote"}:
            raise RegistryError("INVALID_LOCALITY", self.locality)
        if self.locality == "remote" and not self.network_required:
            raise RegistryError(
                "INVALID_NETWORK_POLICY", "remote provider requires network"
            )
        if self.secret_ref is not None and not _SECRET_REF.fullmatch(self.secret_ref):
            raise RegistryError("INVALID_SECRET_REF", "secret_ref must be secret://")
        _assert_secretless(self.metadata)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    provider_id: str
    capabilities: frozenset[Capability]
    context_limit: int
    output_limit: int
    allowed_data: frozenset[DataClassification]
    benchmark: Benchmark
    version: str
    config_hash: str
    health: HealthState = HealthState.HEALTHY
    circuit_open: bool = False
    accessible: bool = True
    embedding_profile: EmbeddingProfile | None = None
    fallback_model_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        capabilities = _tuple_value(self.capabilities, "capabilities")
        allowed_data = _tuple_value(self.allowed_data, "allowed_data")
        try:
            object.__setattr__(
                self,
                "capabilities",
                frozenset(Capability(value) for value in capabilities),
            )
            object.__setattr__(
                self,
                "allowed_data",
                frozenset(DataClassification(value) for value in allowed_data),
            )
            object.__setattr__(self, "health", HealthState(self.health))
        except (TypeError, ValueError) as exc:
            raise RegistryError("INVALID_MODEL_POLICY", "invalid enum value") from exc
        object.__setattr__(
            self,
            "fallback_model_ids",
            _tuple_value(self.fallback_model_ids, "fallback_model_ids"),
        )
        if not isinstance(self.circuit_open, bool) or not isinstance(
            self.accessible, bool
        ):
            raise RegistryError("INVALID_MODEL_POLICY", "booleans must be exact")
        _require_identifier(self.model_id, "model_id")
        _require_identifier(self.provider_id, "provider_id")
        _require_sha256(self.config_hash, "model config_hash")
        _clean_text(self.version, "version", "MODEL_VERSION_NOT_EXACT")
        if self.version.casefold() in _MUTABLE_REFS:
            raise RegistryError("MODEL_VERSION_NOT_EXACT", self.version)
        if self.model_id.casefold() in _MUTABLE_REFS:
            raise RegistryError("MODEL_ID_NOT_EXACT", self.model_id)
        if not self.capabilities or not self.allowed_data:
            raise RegistryError(
                "INVALID_MODEL_POLICY", "capability and data policy required"
            )
        if (
            not _exact_int(self.context_limit)
            or not _exact_int(self.output_limit)
            or self.context_limit <= 0
            or self.output_limit <= 0
        ):
            raise RegistryError("INVALID_MODEL_LIMIT", "limits must be positive")
        if Capability.EMBEDDING in self.capabilities and self.embedding_profile is None:
            raise RegistryError("MISSING_EMBEDDING_PROFILE", self.model_id)
        if self.embedding_profile is not None and not isinstance(
            self.embedding_profile, EmbeddingProfile
        ):
            raise RegistryError("INVALID_MODEL_POLICY", "embedding profile type")
        if (
            Capability.EMBEDDING not in self.capabilities
            and self.embedding_profile is not None
        ):
            raise RegistryError("UNEXPECTED_EMBEDDING_PROFILE", self.model_id)
        for fallback_id in self.fallback_model_ids:
            _require_identifier(fallback_id, "fallback_model_id")
            if fallback_id == self.model_id:
                raise RegistryError(
                    "INVALID_FALLBACK", "model cannot fallback to itself"
                )


@dataclass(frozen=True)
class RouteRequest:
    capability: Capability
    data_classification: DataClassification
    requested_model_id: str | None = None
    required_embedding_profile: EmbeddingProfile | None = None
    max_latency_p95_ms: float | None = None
    max_cost_micro_usd: int | None = None
    min_quality_score: float = 0
    allow_degraded: bool = False

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "capability", Capability(self.capability))
            object.__setattr__(
                self,
                "data_classification",
                DataClassification(self.data_classification),
            )
        except (TypeError, ValueError) as exc:
            raise RegistryError("INVALID_ROUTE_POLICY", "invalid enum value") from exc
        if self.required_embedding_profile is not None and not isinstance(
            self.required_embedding_profile, EmbeddingProfile
        ):
            raise RegistryError("INVALID_ROUTE_POLICY", "embedding profile type")
        if not isinstance(self.allow_degraded, bool):
            raise RegistryError("INVALID_ROUTE_POLICY", "boolean must be exact")
        if (
            not _exact_finite_number(self.min_quality_score)
            or not 0 <= self.min_quality_score <= 1
        ):
            raise RegistryError(
                "INVALID_ROUTE_POLICY", "quality must be between 0 and 1"
            )
        if self.max_latency_p95_ms is not None and (
            not _exact_finite_number(self.max_latency_p95_ms)
            or self.max_latency_p95_ms < 0
        ):
            raise RegistryError("INVALID_ROUTE_POLICY", "latency is invalid")
        if self.max_cost_micro_usd is not None and (
            not _exact_int(self.max_cost_micro_usd) or self.max_cost_micro_usd < 0
        ):
            raise RegistryError("INVALID_ROUTE_POLICY", "cost is invalid")


class ProviderModelRegistry:
    """Routes only exact, accessible records satisfying every policy dimension."""

    def __init__(
        self,
        providers: Iterable[ProviderRecord],
        models: Iterable[ModelRecord],
    ) -> None:
        provider_records = tuple(providers)
        model_records = tuple(models)
        self._providers = {item.provider_id: item for item in provider_records}
        self._models = {item.model_id: item for item in model_records}
        if len(self._providers) != len(provider_records):
            raise RegistryError("DUPLICATE_PROVIDER", "provider ids must be unique")
        if len(self._models) != len(model_records):
            raise RegistryError("DUPLICATE_MODEL", "model ids must be unique")
        missing = sorted(
            {model.provider_id for model in model_records} - set(self._providers)
        )
        if missing:
            raise RegistryError("UNKNOWN_PROVIDER", ",".join(missing))
        for model in model_records:
            unknown = set(model.fallback_model_ids) - set(self._models)
            if unknown:
                raise RegistryError("UNKNOWN_FALLBACK", ",".join(sorted(unknown)))

    def get_provider(self, provider_id: str) -> ProviderRecord:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise RegistryError("PROVIDER_NOT_REGISTERED", provider_id) from exc

    def get_model(self, model_id: str) -> ModelRecord:
        try:
            return self._models[model_id]
        except KeyError as exc:
            raise RegistryError("MODEL_NOT_REGISTERED", model_id) from exc

    def _eligible(self, model: ModelRecord, request: RouteRequest) -> bool:
        provider = self._providers[model.provider_id]
        allowed_health = {HealthState.HEALTHY}
        if request.allow_degraded:
            allowed_health.add(HealthState.DEGRADED)
        if not model.accessible or model.circuit_open or provider.circuit_open:
            return False
        if model.health not in allowed_health or provider.health not in allowed_health:
            return False
        if request.capability not in model.capabilities:
            return False
        if request.data_classification not in model.allowed_data:
            return False
        if model.benchmark.quality_score < request.min_quality_score:
            return False
        if (
            request.max_latency_p95_ms is not None
            and model.benchmark.latency_p95_ms > request.max_latency_p95_ms
        ):
            return False
        if (
            request.max_cost_micro_usd is not None
            and model.benchmark.cost_micro_usd > request.max_cost_micro_usd
        ):
            return False
        required_profile = request.required_embedding_profile
        if required_profile is not None:
            return (
                model.embedding_profile is not None
                and model.embedding_profile.compatibility_key
                == required_profile.compatibility_key
            )
        return True

    def route(self, request: RouteRequest) -> ModelRecord:
        if request.requested_model_id is not None:
            _require_identifier(request.requested_model_id, "requested_model_id")
            model = self._models.get(request.requested_model_id)
            if model is None:
                raise RegistryError("MODEL_NOT_REGISTERED", request.requested_model_id)
            if not model.accessible:
                raise RegistryError("MODEL_NOT_ACCESSIBLE", request.requested_model_id)
            if not self._eligible(model, request):
                raise RegistryError("MODEL_POLICY_REJECTED", request.requested_model_id)
            return model

        candidates = [
            model for model in self._models.values() if self._eligible(model, request)
        ]
        if not candidates:
            raise RegistryError("NO_ELIGIBLE_MODEL", request.capability.value)
        return sorted(
            candidates,
            key=lambda item: (
                -item.benchmark.quality_score,
                item.benchmark.latency_p95_ms,
                item.benchmark.cost_micro_usd,
                item.model_id,
            ),
        )[0]

    def route_fallback(
        self, primary_model_id: str, request: RouteRequest
    ) -> ModelRecord:
        primary = self._models.get(primary_model_id)
        if primary is None:
            raise RegistryError("MODEL_NOT_REGISTERED", primary_model_id)
        failures: list[str] = []
        for fallback_id in primary.fallback_model_ids:
            candidate = self._models[fallback_id]
            if request.capability is Capability.EMBEDDING:
                if (
                    primary.embedding_profile is None
                    or candidate.embedding_profile is None
                ):
                    failures.append(fallback_id)
                    continue
                if (
                    primary.embedding_profile.compatibility_key
                    != candidate.embedding_profile.compatibility_key
                ):
                    failures.append(fallback_id)
                    continue
            if self._eligible(candidate, request):
                return candidate
            failures.append(fallback_id)
        detail = ",".join(failures) if failures else primary_model_id
        raise RegistryError("NO_SAFE_FALLBACK", detail)


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    source_uri: str
    version: str
    commit_sha: str
    package_hash: str
    publisher: str
    owner: str
    trust_level: TrustLevel
    required_tools: frozenset[str]
    required_permissions: frozenset[str]
    network_required: bool
    filesystem_scopes: tuple[str, ...]
    supported_adapters: frozenset[str]
    operation_receipt_refs: tuple[str, ...]
    security_scan_receipt_ref: str
    license_id: str
    enabled_scope: SkillScope
    enabled_scope_id: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "trust_level", TrustLevel(self.trust_level))
            object.__setattr__(self, "enabled_scope", SkillScope(self.enabled_scope))
        except (TypeError, ValueError) as exc:
            raise RegistryError(
                "INCOMPLETE_SKILL_METADATA", "invalid enum value"
            ) from exc
        object.__setattr__(
            self,
            "required_tools",
            frozenset(_tuple_value(self.required_tools, "required_tools")),
        )
        object.__setattr__(
            self,
            "required_permissions",
            frozenset(_tuple_value(self.required_permissions, "required_permissions")),
        )
        object.__setattr__(
            self,
            "supported_adapters",
            frozenset(_tuple_value(self.supported_adapters, "supported_adapters")),
        )
        object.__setattr__(
            self,
            "operation_receipt_refs",
            _tuple_value(self.operation_receipt_refs, "operation_receipt_refs"),
        )
        object.__setattr__(
            self,
            "filesystem_scopes",
            tuple(
                _normalized_absolute_scope(value)
                for value in _tuple_value(self.filesystem_scopes, "filesystem_scopes")
            ),
        )
        if not isinstance(self.network_required, bool) or not isinstance(
            self.enabled, bool
        ):
            raise RegistryError("INCOMPLETE_SKILL_METADATA", "booleans must be exact")
        _require_identifier(self.skill_id, "skill_id")
        _require_sha256(self.package_hash, "package_hash")
        if not _COMMIT_SHA.fullmatch(self.commit_sha):
            raise RegistryError(
                "MUTABLE_SKILL_REF", "exact lowercase commit SHA required"
            )
        _clean_text(self.version, "version", "MUTABLE_SKILL_REF")
        if self.version.casefold() in _MUTABLE_REFS:
            raise RegistryError("MUTABLE_SKILL_REF", self.version)
        _clean_text(self.source_uri, "source_uri", "INVALID_SKILL_SOURCE")
        try:
            parsed_source = urlsplit(self.source_uri)
            source_port = parsed_source.port
        except ValueError as exc:
            raise RegistryError("INVALID_SKILL_SOURCE", self.source_uri) from exc
        if (
            parsed_source.scheme not in {"git+https", "git+ssh"}
            or not parsed_source.hostname
            or not parsed_source.path
            or parsed_source.path == "/"
            or parsed_source.username is not None
            or parsed_source.password is not None
            or _url_contains_credentials(self.source_uri)
            or any(character.isspace() for character in self.source_uri)
            or source_port is not None
            and not 1 <= source_port <= 65535
        ):
            raise RegistryError("INVALID_SKILL_SOURCE", self.source_uri)
        fragment = parsed_source.fragment.casefold()
        if fragment in _MUTABLE_REFS:
            raise RegistryError("MUTABLE_SKILL_REF", self.source_uri)
        if fragment != self.commit_sha:
            raise RegistryError("MUTABLE_SKILL_REF", "source URI must pin commit SHA")
        for value, field_name in (
            (self.publisher, "publisher"),
            (self.owner, "owner"),
            (self.license_id, "license_id"),
        ):
            _clean_text(value, field_name, "INCOMPLETE_SKILL_METADATA")
        if not self.supported_adapters:
            raise RegistryError("INCOMPLETE_SKILL_METADATA", "adapter support required")
        if (
            self.enabled_scope is SkillScope.GLOBAL
            and self.enabled_scope_id is not None
        ):
            raise RegistryError("INVALID_SKILL_SCOPE", "global scope has no scope id")
        if self.enabled_scope is not SkillScope.GLOBAL:
            normalized_scope_id = _clean_text(
                self.enabled_scope_id,
                "enabled_scope_id",
                "INVALID_SKILL_SCOPE",
            )
            object.__setattr__(self, "enabled_scope_id", normalized_scope_id)
            _require_identifier(normalized_scope_id, "enabled_scope_id")
        for field_name, values in (
            ("required_tools", self.required_tools),
            ("required_permissions", self.required_permissions),
            ("supported_adapters", self.supported_adapters),
        ):
            for value in values:
                _require_identifier(value, field_name)
        if self.required_permissions & _AUTHORITY_PERMISSIONS:
            raise RegistryError("AUTHORITY_ESCALATION", self.skill_id)
        _require_receipt_ref(
            self.security_scan_receipt_ref,
            "security_scan_receipt_ref",
        )
        if not self.operation_receipt_refs:
            raise RegistryError("MISSING_OPERATION_RECEIPT", self.skill_id)
        for receipt_ref in self.operation_receipt_refs:
            _require_receipt_ref(receipt_ref, "operation_receipt_ref")

    @property
    def record_hash(self) -> str:
        payload = asdict(self)
        payload["trust_level"] = self.trust_level.value
        payload["enabled_scope"] = self.enabled_scope.value
        for key in (
            "required_tools",
            "required_permissions",
            "supported_adapters",
        ):
            payload[key] = sorted(payload[key])
        return canonical_hash(payload)


@dataclass(frozen=True)
class SkillAdmissionPolicy:
    allowed_trust_levels: frozenset[TrustLevel]
    allowed_tools: frozenset[str]
    allowed_permissions: frozenset[str]
    allow_network: bool
    filesystem_roots: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            allowed_trust_levels = _tuple_value(
                self.allowed_trust_levels,
                "allowed_trust_levels",
            )
            object.__setattr__(
                self,
                "allowed_trust_levels",
                frozenset(TrustLevel(value) for value in allowed_trust_levels),
            )
        except (TypeError, ValueError) as exc:
            raise RegistryError("SKILL_TRUST_REJECTED", "invalid trust level") from exc
        object.__setattr__(
            self,
            "allowed_tools",
            frozenset(_tuple_value(self.allowed_tools, "allowed_tools")),
        )
        object.__setattr__(
            self,
            "allowed_permissions",
            frozenset(_tuple_value(self.allowed_permissions, "allowed_permissions")),
        )
        object.__setattr__(
            self,
            "filesystem_roots",
            tuple(
                _normalized_absolute_scope(value)
                for value in _tuple_value(self.filesystem_roots, "filesystem_roots")
            ),
        )


@dataclass(frozen=True)
class SkillProjection:
    skill_id: str
    adapter_id: str
    commit_sha: str
    package_hash: str
    registry_record_hash: str
    admission_receipt_ref: str
    projection_hash: str
    canonical_authority: str = "postgresql_skill_registry"
    projection_only: bool = True


class SkillRegistry:
    def __init__(self, records: Iterable[SkillRecord]) -> None:
        source = tuple(records)
        self._records = {record.skill_id: record for record in source}
        if len(source) != len(self._records):
            raise RegistryError("DUPLICATE_SKILL", "skill ids must be unique")

    def get(self, skill_id: str) -> SkillRecord:
        try:
            return self._records[skill_id]
        except KeyError as exc:
            raise RegistryError("SKILL_NOT_REGISTERED", skill_id) from exc

    def admit_execution(
        self,
        skill_id: str,
        *,
        observed_package_hash: str,
        adapter_id: str,
        policy: SkillAdmissionPolicy,
        requested_permissions: frozenset[str] = frozenset(),
    ) -> SkillProjection:
        record = self._records.get(skill_id)
        if record is None:
            raise RegistryError("SKILL_NOT_REGISTERED", skill_id)
        _require_sha256(observed_package_hash, "observed_package_hash")
        if not record.enabled:
            raise RegistryError("SKILL_DISABLED", skill_id)
        if observed_package_hash != record.package_hash:
            raise RegistryError("SKILL_HASH_DRIFT", skill_id)
        if adapter_id not in record.supported_adapters:
            raise RegistryError("ADAPTER_NOT_SUPPORTED", adapter_id)
        if record.trust_level not in policy.allowed_trust_levels:
            raise RegistryError("SKILL_TRUST_REJECTED", record.trust_level.value)
        if not record.required_tools.issubset(policy.allowed_tools):
            raise RegistryError("TOOL_SCOPE_REJECTED", skill_id)
        effective_permissions = record.required_permissions | requested_permissions
        if effective_permissions & _AUTHORITY_PERMISSIONS:
            raise RegistryError("AUTHORITY_ESCALATION", skill_id)
        if not effective_permissions.issubset(policy.allowed_permissions):
            raise RegistryError("PERMISSION_SCOPE_REJECTED", skill_id)
        if record.network_required and not policy.allow_network:
            raise RegistryError("NETWORK_SCOPE_REJECTED", skill_id)
        if any(
            not any(
                PurePosixPath(scope).is_relative_to(PurePosixPath(root))
                for root in policy.filesystem_roots
            )
            for scope in record.filesystem_scopes
        ):
            raise RegistryError("FILESYSTEM_SCOPE_REJECTED", skill_id)

        admission_receipt = record.operation_receipt_refs[-1]
        payload = {
            "adapter_id": adapter_id,
            "admission_receipt_ref": admission_receipt,
            "commit_sha": record.commit_sha,
            "package_hash": record.package_hash,
            "registry_record_hash": record.record_hash,
            "skill_id": skill_id,
        }
        return SkillProjection(
            skill_id=skill_id,
            adapter_id=adapter_id,
            commit_sha=record.commit_sha,
            package_hash=record.package_hash,
            registry_record_hash=record.record_hash,
            admission_receipt_ref=admission_receipt,
            projection_hash=canonical_hash(payload),
        )
