from dataclasses import replace

import pytest

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
    RegistryError,
    RouteRequest,
    SkillAdmissionPolicy,
    SkillRecord,
    SkillRegistry,
    SkillScope,
    TrustLevel,
    canonical_hash,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
COMMIT = "d" * 40


def provider(
    provider_id: str = "local-bge",
    *,
    health: HealthState = HealthState.HEALTHY,
    secret_ref: str | None = None,
) -> ProviderRecord:
    return ProviderRecord(
        provider_id=provider_id,
        adapter_id="openai-compatible",
        locality="local" if provider_id.startswith("local") else "remote",
        network_required=not provider_id.startswith("local"),
        health=health,
        circuit_open=False,
        version="1.0.0",
        config_hash=HASH_A,
        secret_ref=secret_ref,
        metadata={"endpoint_ref": "config://providers/bge"},
    )


def profile(
    profile_id: str = "bge-m3-v1",
    config_hash: str = HASH_B,
) -> EmbeddingProfile:
    return EmbeddingProfile(
        profile_id=profile_id,
        dimension=1024,
        distance=DistanceMetric.COSINE,
        query_prefix="query: ",
        document_prefix="passage: ",
        config_hash=config_hash,
    )


def model(
    model_id: str,
    provider_id: str = "local-bge",
    *,
    embedding_profile: EmbeddingProfile | None = None,
    accessible: bool = True,
    health: HealthState = HealthState.HEALTHY,
    quality: float = 0.8,
    latency: float = 20,
    cost: int = 0,
    allowed_data: frozenset[DataClassification] | None = None,
    fallbacks: tuple[str, ...] = (),
) -> ModelRecord:
    capabilities = frozenset(
        {Capability.EMBEDDING} if embedding_profile else {Capability.CHAT}
    )
    return ModelRecord(
        model_id=model_id,
        provider_id=provider_id,
        capabilities=capabilities,
        context_limit=8192,
        output_limit=2048,
        allowed_data=allowed_data
        or frozenset(
            {
                DataClassification.PUBLIC,
                DataClassification.INTERNAL,
                DataClassification.CONFIDENTIAL,
            }
        ),
        benchmark=Benchmark(
            latency_p95_ms=latency,
            cost_micro_usd=cost,
            quality_score=quality,
            sample_size=25,
        ),
        version="2026-09-01",
        config_hash=HASH_C,
        health=health,
        accessible=accessible,
        embedding_profile=embedding_profile,
        fallback_model_ids=fallbacks,
    )


def skill(**overrides) -> SkillRecord:
    values = {
        "skill_id": "science-review",
        "source_uri": "git+https://example.test/skills.git#" + COMMIT,
        "version": "1.2.3",
        "commit_sha": COMMIT,
        "package_hash": HASH_A,
        "publisher": "research-team",
        "owner": "context-owner",
        "trust_level": TrustLevel.VERIFIED,
        "required_tools": frozenset({"read_file"}),
        "required_permissions": frozenset({"read_project"}),
        "network_required": False,
        "filesystem_scopes": ("/workspace/project/docs",),
        "supported_adapters": frozenset({"opencode", "claude", "codex"}),
        "operation_receipt_refs": ("receipt://skills/install-001",),
        "security_scan_receipt_ref": "receipt://skills/scan-001",
        "license_id": "Apache-2.0",
        "enabled_scope": SkillScope.PROJECT,
        "enabled_scope_id": "project-1",
    }
    values.update(overrides)
    return SkillRecord(**values)


def admission_policy() -> SkillAdmissionPolicy:
    return SkillAdmissionPolicy(
        allowed_trust_levels=frozenset({TrustLevel.VERIFIED}),
        allowed_tools=frozenset({"read_file"}),
        allowed_permissions=frozenset({"read_project"}),
        allow_network=False,
        filesystem_roots=("/workspace/project",),
    )


def test_exact_requested_model_must_exist_and_be_accessible() -> None:
    registry = ProviderModelRegistry(
        [provider()],
        [model("bge-m3@exact", accessible=False), model("other@exact")],
    )
    request = RouteRequest(
        Capability.CHAT,
        DataClassification.INTERNAL,
        requested_model_id="bge-m3@exact",
    )
    with pytest.raises(RegistryError) as error:
        registry.route(request)
    assert error.value.code == "MODEL_NOT_ACCESSIBLE"
    with pytest.raises(RegistryError) as missing:
        registry.route(
            replace(request, requested_model_id="alias-that-is-not-registered")
        )
    assert missing.value.code == "MODEL_NOT_REGISTERED"


def test_routing_uses_capability_data_health_and_benchmark() -> None:
    assert DataClassification.INTERNAL.value == "INTERNAL"
    assert DataClassification.CONFIDENTIAL.value == "CONFIDENTIAL"
    providers = [provider(), provider("remote-chat", health=HealthState.DEGRADED)]
    records = [
        model("local-low-quality", quality=0.70, latency=5),
        model(
            "remote-high-quality",
            "remote-chat",
            quality=0.99,
            latency=10,
            cost=20,
            allowed_data=frozenset({DataClassification.PUBLIC}),
        ),
        model("local-best-eligible", quality=0.91, latency=18, cost=3),
        model("local-over-budget", quality=0.98, latency=200, cost=3),
    ]
    registry = ProviderModelRegistry(providers, records)
    routed = registry.route(
        RouteRequest(
            Capability.CHAT,
            DataClassification.CONFIDENTIAL,
            min_quality_score=0.8,
            max_latency_p95_ms=50,
            max_cost_micro_usd=10,
        )
    )
    assert routed.model_id == "local-best-eligible"


def test_invalid_route_benchmark_policy_is_rejected() -> None:
    with pytest.raises(RegistryError) as error:
        RouteRequest(
            Capability.CHAT,
            DataClassification.INTERNAL,
            min_quality_score=1.1,
        )
    assert error.value.code == "INVALID_ROUTE_POLICY"


def test_embedding_fallback_cannot_silently_change_profile() -> None:
    pinned = profile()
    incompatible = replace(pinned, config_hash=HASH_C)
    registry = ProviderModelRegistry(
        [provider()],
        [
            model("primary", embedding_profile=pinned, fallbacks=("wrong-profile",)),
            model("wrong-profile", embedding_profile=incompatible),
        ],
    )
    request = RouteRequest(
        Capability.EMBEDDING,
        DataClassification.INTERNAL,
        required_embedding_profile=pinned,
    )
    with pytest.raises(RegistryError) as error:
        registry.route_fallback("primary", request)
    assert error.value.code == "NO_SAFE_FALLBACK"


def test_embedding_fallback_accepts_only_exact_compatible_profile() -> None:
    pinned = profile()
    registry = ProviderModelRegistry(
        [provider()],
        [
            model("primary", embedding_profile=pinned, fallbacks=("compatible",)),
            model("compatible", embedding_profile=pinned),
        ],
    )
    routed = registry.route_fallback(
        "primary",
        RouteRequest(
            Capability.EMBEDDING,
            DataClassification.CONFIDENTIAL,
            required_embedding_profile=pinned,
        ),
    )
    assert routed.model_id == "compatible"
    assert routed.embedding_profile == pinned


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_key": "plain-text"},
        {"nested": {"password": "plain-text"}},
        {"header": "Bearer token-value"},
        {"value": "sk-not-allowed"},
    ],
)
def test_provider_metadata_is_secretless(metadata: dict) -> None:
    with pytest.raises(RegistryError) as error:
        ProviderRecord(
            **{
                **provider().__dict__,
                "metadata": metadata,
            }
        )
    assert error.value.code == "SECRET_IN_METADATA"


def test_provider_uses_secret_store_reference_not_secret_value() -> None:
    record = provider("remote-chat", secret_ref="secret://providers/chat/api-key")
    assert record.secret_ref == "secret://providers/chat/api-key"
    with pytest.raises(TypeError):
        record.metadata["endpoint_ref"] = "config://attacker"
    with pytest.raises(RegistryError) as error:
        provider("remote-chat", secret_ref="plain-text-key")
    assert error.value.code == "INVALID_SECRET_REF"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "latest"),
        ("version", "main"),
        ("commit_sha", "main"),
        ("source_uri", "git+https://example.test/skills.git#main"),
    ],
)
def test_mutable_skill_references_are_rejected(field: str, value: str) -> None:
    with pytest.raises(RegistryError) as error:
        skill(**{field: value})
    assert error.value.code == "MUTABLE_SKILL_REF"


def test_skill_hash_drift_fails_execution_admission() -> None:
    registry = SkillRegistry([skill()])
    with pytest.raises(RegistryError) as error:
        registry.admit_execution(
            "science-review",
            observed_package_hash=HASH_B,
            adapter_id="codex",
            policy=admission_policy(),
        )
    assert error.value.code == "SKILL_HASH_DRIFT"


def test_skill_cannot_escalate_root_authority_or_security_policy() -> None:
    with pytest.raises(RegistryError) as registration_error:
        skill(required_permissions=frozenset({"modify_security_policy"}))
    assert registration_error.value.code == "AUTHORITY_ESCALATION"

    registry = SkillRegistry([skill()])
    with pytest.raises(RegistryError) as execution_error:
        registry.admit_execution(
            "science-review",
            observed_package_hash=HASH_A,
            adapter_id="codex",
            policy=admission_policy(),
            requested_permissions=frozenset({"change_root_authority"}),
        )
    assert execution_error.value.code == "AUTHORITY_ESCALATION"


def test_skill_projection_is_registry_and_receipt_bound() -> None:
    record = skill()
    projection = SkillRegistry([record]).admit_execution(
        record.skill_id,
        observed_package_hash=record.package_hash,
        adapter_id="claude",
        policy=admission_policy(),
    )
    assert projection.registry_record_hash == record.record_hash
    assert projection.admission_receipt_ref == "receipt://skills/install-001"
    assert projection.canonical_authority == "postgresql_skill_registry"
    assert projection.projection_only is True
    assert len(projection.projection_hash) == 64


def test_skill_execution_policy_bounds_network_and_filesystem() -> None:
    networked = skill(network_required=True)
    with pytest.raises(RegistryError) as network_error:
        SkillRegistry([networked]).admit_execution(
            networked.skill_id,
            observed_package_hash=networked.package_hash,
            adapter_id="codex",
            policy=admission_policy(),
        )
    assert network_error.value.code == "NETWORK_SCOPE_REJECTED"

    escaped = skill(filesystem_scopes=("/workspace/other",))
    with pytest.raises(RegistryError) as filesystem_error:
        SkillRegistry([escaped]).admit_execution(
            escaped.skill_id,
            observed_package_hash=escaped.package_hash,
            adapter_id="codex",
            policy=admission_policy(),
        )
    assert filesystem_error.value.code == "FILESYSTEM_SCOPE_REJECTED"


@pytest.mark.parametrize(
    "scope",
    (
        "/workspace/project/../../etc",
        "/workspace/project/./docs",
        "workspace/project/docs",
        "/workspace//project/docs",
    ),
)
def test_skill_filesystem_scopes_must_be_normalized_absolute_paths(scope: str) -> None:
    with pytest.raises(RegistryError) as error:
        skill(filesystem_scopes=(scope,))
    assert error.value.code == "INVALID_FILESYSTEM_SCOPE"


def test_skill_policy_roots_reject_lexical_traversal() -> None:
    with pytest.raises(RegistryError) as error:
        SkillAdmissionPolicy(
            allowed_trust_levels=frozenset({TrustLevel.VERIFIED}),
            allowed_tools=frozenset({"read_file"}),
            allowed_permissions=frozenset({"read_project"}),
            allow_network=False,
            filesystem_roots=("/workspace/project/../secrets",),
        )
    assert error.value.code == "INVALID_FILESYSTEM_SCOPE"


def test_model_and_skill_collections_are_normalized_to_immutable_exact_types() -> None:
    capabilities = {"chat"}
    classifications = {"INTERNAL"}
    record = model("normalized")
    normalized = replace(
        record,
        capabilities=capabilities,
        allowed_data=classifications,
        health="healthy",
        fallback_model_ids=[],
    )
    capabilities.add("tool_use")
    classifications.add("RESTRICTED")
    assert normalized.capabilities == frozenset({Capability.CHAT})
    assert normalized.allowed_data == frozenset({DataClassification.INTERNAL})
    assert normalized.health is HealthState.HEALTHY
    assert normalized.fallback_model_ids == ()

    tools = {"read_file"}
    adapters = {"codex"}
    normalized_skill = skill(
        required_tools=tools,
        supported_adapters=adapters,
        operation_receipt_refs=["receipt://skills/install-001"],
        enabled_scope="project",
    )
    tools.add("shell")
    adapters.add("untrusted-cli")
    assert normalized_skill.required_tools == frozenset({"read_file"})
    assert normalized_skill.supported_adapters == frozenset({"codex"})
    assert normalized_skill.operation_receipt_refs == ("receipt://skills/install-001",)
    assert normalized_skill.enabled_scope is SkillScope.PROJECT


@pytest.mark.parametrize(
    "metadata",
    (
        {"authorization": "Basic dXNlcjpwYXNz"},
        {"header": "ghp_0123456789abcdefghijklmnopqrstuvwxyz"},
        {"header": "basic dXNlcjpwYXNz"},
    ),
)
def test_provider_metadata_rejects_additional_credential_shapes(metadata: dict) -> None:
    with pytest.raises(RegistryError) as error:
        ProviderRecord(**{**provider().__dict__, "metadata": metadata})
    assert error.value.code == "SECRET_IN_METADATA"


def test_skill_version_must_be_nonempty_and_exact() -> None:
    with pytest.raises(RegistryError) as error:
        skill(version="")
    assert error.value.code == "MUTABLE_SKILL_REF"


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_canonical_registry_json_rejects_nonfinite_numbers(value: float) -> None:
    with pytest.raises(RegistryError) as error:
        canonical_hash({"benchmark": value})
    assert error.value.code == "NON_CANONICAL_JSON"


@pytest.mark.parametrize(
    "metadata",
    (
        {"header": "   Basic dXNlcjpwYXNz"},
        {"header": "\tBearer token-value"},
        {"endpoint_ref": "https://user:password@example.test/v1"},
        {"endpoint_ref": "https://example.test/v1?api_key=plaintext"},
        {"endpoint_ref": "https://example.test/v1?access%5Ftoken=plaintext"},
    ),
)
def test_provider_metadata_rejects_whitespace_and_url_credentials(
    metadata: dict,
) -> None:
    with pytest.raises(RegistryError) as error:
        ProviderRecord(**{**provider().__dict__, "metadata": metadata})
    assert error.value.code == "SECRET_IN_METADATA"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("latency_p95_ms", True),
        ("latency_p95_ms", "10"),
        ("latency_p95_ms", float("nan")),
        ("cost_micro_usd", True),
        ("cost_micro_usd", 1.5),
        ("quality_score", True),
        ("quality_score", float("inf")),
        ("sample_size", True),
        ("sample_size", 2.5),
    ),
)
def test_benchmark_numeric_fields_are_exact_finite_types(field: str, value) -> None:
    values = {
        "latency_p95_ms": 10.0,
        "cost_micro_usd": 1,
        "quality_score": 0.8,
        "sample_size": 4,
    }
    values[field] = value
    with pytest.raises(RegistryError) as error:
        Benchmark(**values)
    assert error.value.code == "INVALID_BENCHMARK"


@pytest.mark.parametrize("dimension", (True, 1024.0, float("nan")))
def test_embedding_dimension_is_an_exact_positive_integer(dimension) -> None:
    with pytest.raises(RegistryError) as error:
        EmbeddingProfile(
            profile_id="bge",
            dimension=dimension,
            distance=DistanceMetric.COSINE,
            query_prefix="query: ",
            document_prefix="passage: ",
            config_hash=HASH_A,
        )
    assert error.value.code == "INVALID_EMBEDDING_PROFILE"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("context_limit", True),
        ("context_limit", 8192.0),
        ("output_limit", True),
        ("output_limit", float("inf")),
        ("circuit_open", 0),
        ("accessible", 1),
    ),
)
def test_model_numeric_and_boolean_fields_are_exact_types(field: str, value) -> None:
    with pytest.raises(RegistryError) as error:
        replace(model("typed-model"), **{field: value})
    assert error.value.code in {"INVALID_MODEL_LIMIT", "INVALID_MODEL_POLICY"}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_latency_p95_ms", True),
        ("max_latency_p95_ms", "10"),
        ("max_latency_p95_ms", float("nan")),
        ("max_cost_micro_usd", True),
        ("max_cost_micro_usd", 1.5),
        ("min_quality_score", True),
        ("min_quality_score", float("inf")),
        ("allow_degraded", 1),
    ),
)
def test_route_request_numeric_and_boolean_fields_are_exact_types(
    field: str,
    value,
) -> None:
    values = {
        "capability": Capability.CHAT,
        "data_classification": DataClassification.INTERNAL,
        field: value,
    }
    with pytest.raises(RegistryError) as error:
        RouteRequest(**values)
    assert error.value.code == "INVALID_ROUTE_POLICY"


@pytest.mark.parametrize(
    "source_uri",
    (
        "file:///workspace/skill",
        "git+https://",
        "https://example.test/skill.git",
        "git+https://user:password@example.test/skill.git#" + COMMIT,
        "git+https://example.test/skill.git?api_key=value#" + COMMIT,
    ),
)
def test_skill_source_must_be_valid_credentialless_immutable_git_uri(
    source_uri: str,
) -> None:
    with pytest.raises(RegistryError) as error:
        skill(source_uri=source_uri)
    assert error.value.code == "INVALID_SKILL_SOURCE"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("publisher", " "),
        ("publisher", " owner"),
        ("owner", "\t"),
        ("license_id", "Apache-2.0 "),
        ("enabled_scope_id", " "),
        ("required_tools", frozenset({"read_file", " "})),
        ("required_permissions", frozenset({"read_project", "\t"})),
        ("supported_adapters", frozenset({"codex", " "})),
    ),
)
def test_skill_text_and_collection_items_reject_whitespace(
    field: str,
    value,
) -> None:
    with pytest.raises(RegistryError) as error:
        skill(**{field: value})
    assert error.value.code in {
        "INCOMPLETE_SKILL_METADATA",
        "INVALID_IDENTIFIER",
        "INVALID_SKILL_SCOPE",
    }
