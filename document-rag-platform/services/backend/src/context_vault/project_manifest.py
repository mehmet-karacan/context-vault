"""Strict, immutable contract for ``.contextvault/project.yaml``.

The human-authored and generated portions deliberately use separate schemas.  A
generated payload is accepted only when its canonical SHA-256 matches, making a
manual edit fail closed instead of silently becoming project authority.
"""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

import yaml  # type: ignore[import-untyped]


class ManifestValidationError(ValueError):
    """The project manifest is malformed or violates an integrity rule."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ManifestValidationError(
                    "YAML mapping key must be hashable"
                ) from exc
            if duplicate:
                raise ManifestValidationError(f"duplicate YAML key: {key!r}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class RepositoryMapping:
    root: str
    remote: str | None


@dataclass(frozen=True)
class SourceRule:
    path: str
    load_tier: str


@dataclass(frozen=True)
class TokenBudget:
    context_window: int
    reserved_output: int
    safety_margin: int

    @property
    def available_input(self) -> int:
        return self.context_window - self.reserved_output - self.safety_margin


@dataclass(frozen=True)
class ProviderDataPolicy:
    remote_allowed_classifications: tuple[str, ...]
    local_allowed_classifications: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalPolicy:
    owner: str
    approvers: tuple[str, ...]
    human_required_for: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedSection:
    generator: str
    generator_version: str
    source_hash: str
    payload_json: str

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json)


@dataclass(frozen=True)
class ProjectManifest:
    schema_version: int
    project_id: UUID
    repository: RepositoryMapping
    context_sources: tuple[SourceRule, ...]
    ignored_paths: tuple[str, ...]
    token_budget: TokenBudget
    provider_data_policy: ProviderDataPolicy
    validation_commands: tuple[str, ...]
    approval_policy: ApprovalPolicy
    generated: GeneratedSection | None

    @property
    def manifest_hash(self) -> str:
        payload = asdict(self)
        payload["project_id"] = str(self.project_id)
        return _sha256(payload)


@dataclass(frozen=True)
class RecognizedProject:
    root: Path
    manifest_path: Path
    manifest: ProjectManifest


_TOP_LEVEL_KEYS = {"schema_version", "project_id", "human", "generated"}
_HUMAN_KEYS = {
    "repository",
    "context_sources",
    "ignored_paths",
    "token_budget",
    "provider_data_policy",
    "validation_commands",
    "approval_policy",
}
_TIERS = {
    "MUST_LOAD",
    "SHOULD_LOAD_IF_RELEVANT",
    "RETRIEVE_ON_DEMAND",
    "NEVER_AUTO_LOAD",
}
_CLASSIFICATIONS = {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}
_SHA256_LENGTH = 64
_MAX_MANIFEST_BYTES = 1_000_000


def generated_payload_hash(payload: Any) -> str:
    """Return the integrity hash a manifest generator must persist."""

    return _sha256(payload)


def validate_project_manifest(raw: Mapping[str, Any]) -> ProjectManifest:
    """Validate untrusted decoded YAML and return a deeply immutable contract."""

    data = _mapping(raw, "manifest")
    _exact_keys(
        data, _TOP_LEVEL_KEYS, {"schema_version", "project_id", "human"}, "manifest"
    )
    if (
        isinstance(data["schema_version"], bool)
        or not isinstance(data["schema_version"], int)
        or data["schema_version"] != 1
    ):
        raise ManifestValidationError("schema_version must be exactly 1")
    try:
        project_id = UUID(_string(data["project_id"], "project_id"))
    except ValueError as exc:
        raise ManifestValidationError("project_id must be a UUID") from exc

    human = _mapping(data["human"], "human")
    _exact_keys(human, _HUMAN_KEYS, _HUMAN_KEYS, "human")

    repository_raw = _mapping(human["repository"], "human.repository")
    _exact_keys(repository_raw, {"root", "remote"}, {"root"}, "human.repository")
    root = _safe_path(repository_raw["root"], "human.repository.root", allow_dot=True)
    remote_value = repository_raw.get("remote")
    remote = None if remote_value is None else _credentialless_remote(remote_value)

    sources_raw = _list(human["context_sources"], "human.context_sources")
    if not sources_raw:
        raise ManifestValidationError("human.context_sources cannot be empty")
    sources: list[SourceRule] = []
    seen_paths: set[str] = set()
    for index, source_value in enumerate(sources_raw):
        label = f"human.context_sources[{index}]"
        source = _mapping(source_value, label)
        _exact_keys(source, {"path", "load_tier"}, {"path", "load_tier"}, label)
        path = _safe_path(source["path"], f"{label}.path")
        tier = _choice(source["load_tier"], _TIERS, f"{label}.load_tier")
        if path in seen_paths:
            raise ManifestValidationError(f"duplicate context source path: {path}")
        seen_paths.add(path)
        sources.append(SourceRule(path=path, load_tier=tier))

    ignored = tuple(
        _safe_path(value, f"human.ignored_paths[{index}]")
        for index, value in enumerate(
            _list(human["ignored_paths"], "human.ignored_paths")
        )
    )
    if len(set(ignored)) != len(ignored):
        raise ManifestValidationError("human.ignored_paths contains duplicates")

    budget_raw = _mapping(human["token_budget"], "human.token_budget")
    budget_keys = {"context_window", "reserved_output", "safety_margin"}
    _exact_keys(budget_raw, budget_keys, budget_keys, "human.token_budget")
    budget = TokenBudget(
        context_window=_positive_int(budget_raw["context_window"], "context_window"),
        reserved_output=_nonnegative_int(
            budget_raw["reserved_output"], "reserved_output"
        ),
        safety_margin=_nonnegative_int(budget_raw["safety_margin"], "safety_margin"),
    )
    if budget.available_input <= 0:
        raise ManifestValidationError("token budget leaves no room for input")

    policy_raw = _mapping(human["provider_data_policy"], "human.provider_data_policy")
    policy_keys = {"remote_allowed_classifications", "local_allowed_classifications"}
    _exact_keys(policy_raw, policy_keys, policy_keys, "human.provider_data_policy")
    provider_policy = ProviderDataPolicy(
        remote_allowed_classifications=_choices(
            policy_raw["remote_allowed_classifications"],
            _CLASSIFICATIONS,
            "remote_allowed_classifications",
        ),
        local_allowed_classifications=_choices(
            policy_raw["local_allowed_classifications"],
            _CLASSIFICATIONS,
            "local_allowed_classifications",
        ),
    )

    commands = tuple(
        _string(command, f"human.validation_commands[{index}]")
        for index, command in enumerate(
            _list(human["validation_commands"], "human.validation_commands")
        )
    )
    if not commands:
        raise ManifestValidationError("human.validation_commands cannot be empty")

    approval_raw = _mapping(human["approval_policy"], "human.approval_policy")
    approval_keys = {"owner", "approvers", "human_required_for"}
    _exact_keys(approval_raw, approval_keys, approval_keys, "human.approval_policy")
    approval = ApprovalPolicy(
        owner=_string(approval_raw["owner"], "human.approval_policy.owner"),
        approvers=_unique_strings(
            approval_raw["approvers"], "approvers", require_any=True
        ),
        human_required_for=_unique_strings(
            approval_raw["human_required_for"], "human_required_for"
        ),
    )

    generated_raw = data.get("generated")
    generated = None if generated_raw is None else _generated_section(generated_raw)
    return ProjectManifest(
        schema_version=1,
        project_id=project_id,
        repository=RepositoryMapping(root=root, remote=remote),
        context_sources=tuple(sources),
        ignored_paths=ignored,
        token_budget=budget,
        provider_data_policy=provider_policy,
        validation_commands=commands,
        approval_policy=approval,
        generated=generated,
    )


def load_project_manifest(path: Path) -> ProjectManifest:
    """Load a bounded YAML manifest without resolving any project paths."""

    if path.name not in {"project.yaml", "project.yml"}:
        raise ManifestValidationError(
            "manifest filename must be project.yaml or project.yml"
        )
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ManifestValidationError(
                "project manifest must be a regular non-symlink file"
            )
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise ManifestValidationError(f"cannot read project manifest: {exc}") from exc
    if len(raw_bytes) > _MAX_MANIFEST_BYTES:
        raise ManifestValidationError("project manifest exceeds 1 MB")
    try:
        decoded = yaml.load(raw_bytes, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ManifestValidationError("project manifest is not valid YAML") from exc
    return validate_project_manifest(_mapping(decoded, "manifest"))


def discover_project(start: Path) -> RecognizedProject:
    """Recognize a project by walking upward to its canonical manifest."""

    try:
        cursor = start.resolve(strict=True)
    except OSError as exc:
        raise ManifestValidationError(
            f"project discovery start does not exist: {exc}"
        ) from exc
    if cursor.is_file():
        cursor = cursor.parent
    while True:
        for filename in ("project.yaml", "project.yml"):
            manifest_path = cursor / ".contextvault" / filename
            if manifest_path.exists() or manifest_path.is_symlink():
                manifest = load_project_manifest(manifest_path)
                project_root = (cursor / manifest.repository.root).resolve()
                if not project_root.is_relative_to(cursor):
                    raise ManifestValidationError(
                        "repository root mapping escapes project"
                    )
                return RecognizedProject(
                    root=project_root,
                    manifest_path=manifest_path,
                    manifest=manifest,
                )
        if cursor.parent == cursor:
            raise ManifestValidationError("no .contextvault/project.yaml found")
        cursor = cursor.parent


def _generated_section(value: Any) -> GeneratedSection:
    raw = _mapping(value, "generated")
    keys = {"generator", "generator_version", "source_hash", "payload"}
    _exact_keys(raw, keys, keys, "generated")
    source_hash = _string(raw["source_hash"], "generated.source_hash")
    if len(source_hash) != _SHA256_LENGTH or any(
        c not in "0123456789abcdef" for c in source_hash
    ):
        raise ManifestValidationError(
            "generated.source_hash must be a lowercase SHA-256"
        )
    payload_json = _canonical_json(raw["payload"])
    if hashlib.sha256(payload_json.encode()).hexdigest() != source_hash:
        raise ManifestValidationError("generated payload integrity check failed")
    return GeneratedSection(
        generator=_string(raw["generator"], "generated.generator"),
        generator_version=_string(
            raw["generator_version"], "generated.generator_version"
        ),
        source_hash=source_hash,
        payload_json=payload_json,
    )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ManifestValidationError(f"{label} must be an object with string keys")
    return dict(value)


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestValidationError(f"{label} must be a list")
    return value


def _exact_keys(
    value: Mapping[str, Any], allowed: set[str], required: set[str], label: str
) -> None:
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise ManifestValidationError(
            f"{label} has unknown fields: {', '.join(unknown)}"
        )
    if missing:
        raise ManifestValidationError(
            f"{label} is missing fields: {', '.join(missing)}"
        )


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ManifestValidationError(f"{label} must be a non-empty trimmed string")
    return value


def _credentialless_remote(value: Any) -> str:
    remote = _string(value, "human.repository.remote")
    if any(character.isspace() for character in remote):
        raise ManifestValidationError("human.repository.remote must be credentialless")
    if remote.startswith("git@"):
        host_and_path = remote.removeprefix("git@")
        if ":" not in host_and_path:
            raise ManifestValidationError(
                "human.repository.remote must be a canonical Git remote"
            )
        host, path = host_and_path.split(":", 1)
        if not host or not path or path.startswith("/") or "@" in host:
            raise ManifestValidationError(
                "human.repository.remote must be a canonical Git remote"
            )
        return remote
    try:
        parsed = urlsplit(remote)
        port = parsed.port
    except ValueError as exc:
        raise ManifestValidationError(
            "human.repository.remote must be a canonical Git remote"
        ) from exc
    if (
        parsed.scheme not in {"https", "ssh"}
        or not parsed.hostname
        or not parsed.path
        or parsed.path == "/"
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
        or parsed.username not in {None, "git"}
    ):
        raise ManifestValidationError("human.repository.remote must be credentialless")
    return remote


def _safe_path(value: Any, label: str, *, allow_dot: bool = False) -> str:
    path = _string(value, label).replace("\\", "/")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or (path == "." and not allow_dot):
        raise ManifestValidationError(f"{label} must be a safe relative path")
    return path


def _choice(value: Any, choices: set[str], label: str) -> str:
    text = _string(value, label)
    if text not in choices:
        raise ManifestValidationError(f"{label} must be one of {sorted(choices)}")
    return text


def _choices(value: Any, choices: set[str], label: str) -> tuple[str, ...]:
    selected = _unique_strings(value, label)
    for item in selected:
        _choice(item, choices, label)
    return selected


def _unique_strings(
    value: Any, label: str, *, require_any: bool = False
) -> tuple[str, ...]:
    items = tuple(_string(item, label) for item in _list(value, label))
    if require_any and not items:
        raise ManifestValidationError(f"{label} cannot be empty")
    if len(set(items)) != len(items):
        raise ManifestValidationError(f"{label} contains duplicates")
    return items


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ManifestValidationError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ManifestValidationError(f"{label} must be a non-negative integer")
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError("value is not canonical JSON") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()
