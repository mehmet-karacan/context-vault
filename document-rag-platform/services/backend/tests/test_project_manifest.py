from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from src.context_vault.project_manifest import (
    ManifestValidationError,
    discover_project,
    generated_payload_hash,
    load_project_manifest,
    validate_project_manifest,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _manifest() -> dict[str, object]:
    payload = {"detected_languages": ["python", "typescript"], "file_count": 20}
    return {
        "schema_version": 1,
        "project_id": "3e1d6a52-f72e-4f45-982a-b64fa47fa48d",
        "human": {
            "repository": {"root": ".", "remote": "https://example.invalid/repo.git"},
            "context_sources": [
                {"path": "AKTIF_GOREV.md", "load_tier": "MUST_LOAD"},
                {"path": "private", "load_tier": "NEVER_AUTO_LOAD"},
            ],
            "ignored_paths": [".git"],
            "token_budget": {
                "context_window": 8192,
                "reserved_output": 1024,
                "safety_margin": 512,
            },
            "provider_data_policy": {
                "remote_allowed_classifications": ["PUBLIC", "INTERNAL"],
                "local_allowed_classifications": [
                    "PUBLIC",
                    "INTERNAL",
                    "CONFIDENTIAL",
                    "RESTRICTED",
                ],
            },
            "validation_commands": ["uv run pytest -q"],
            "approval_policy": {
                "owner": "owner",
                "approvers": ["owner"],
                "human_required_for": ["release"],
            },
        },
        "generated": {
            "generator": "cv-project-scan",
            "generator_version": "1.0.0",
            "source_hash": generated_payload_hash(payload),
            "payload": payload,
        },
    }


def test_repository_manifest_is_schema_valid_and_loadable() -> None:
    schema_path = REPOSITORY_ROOT / ".contextvault/project-manifest-v1.schema.json"
    manifest_path = REPOSITORY_ROOT / ".contextvault/project.yaml"
    schema = json.loads(schema_path.read_text())
    raw = yaml.safe_load(manifest_path.read_text())

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(raw)
    manifest = load_project_manifest(manifest_path)

    assert str(manifest.project_id) == raw["project_id"]
    assert manifest.repository.root == "."
    assert manifest.token_budget.available_input == 22528
    assert [source.path for source in manifest.context_sources[:3]] == [
        "AKTIF_GOREV.md",
        "PROJECT_CONTEXT.md",
        "SECURITY.md",
    ]


def test_cli_can_recognize_project_from_nested_working_directory() -> None:
    recognized = discover_project(Path(__file__).parent)

    assert recognized.root == REPOSITORY_ROOT
    assert recognized.manifest_path == REPOSITORY_ROOT / ".contextvault/project.yaml"
    assert recognized.manifest.repository.remote is not None
    assert recognized.manifest.repository.remote.endswith("context-vault.git")


def test_human_and_generated_sources_are_separate_and_generated_is_sealed() -> None:
    raw = _manifest()
    manifest = validate_project_manifest(raw)

    assert manifest.approval_policy.owner == "owner"
    assert manifest.generated is not None
    assert manifest.generated.payload == {
        "detected_languages": ["python", "typescript"],
        "file_count": 20,
    }
    with pytest.raises(FrozenInstanceError):
        manifest.repository.root = "elsewhere"  # type: ignore[misc]

    raw["generated"]["payload"]["file_count"] = 21  # type: ignore[index]
    with pytest.raises(ManifestValidationError, match="integrity"):
        validate_project_manifest(raw)


def test_manifest_hash_is_canonical_and_independent_of_mapping_order() -> None:
    first = _manifest()
    second = dict(reversed(list(first.items())))

    assert (
        validate_project_manifest(first).manifest_hash
        == validate_project_manifest(second).manifest_hash
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.update({"unexpected": True}), "unknown fields"),
        (
            lambda raw: raw["human"]["context_sources"].append(  # type: ignore[index]
                {"path": "../escape", "load_tier": "MUST_LOAD"}
            ),
            "safe relative path",
        ),
        (
            lambda raw: raw["human"]["token_budget"].update(  # type: ignore[index]
                {"reserved_output": 8000, "safety_margin": 1000}
            ),
            "no room",
        ),
    ],
)
def test_manifest_rejects_unknown_fields_path_escape_and_impossible_budget(
    mutation, message: str
) -> None:
    raw = _manifest()
    mutation(raw)
    with pytest.raises(ManifestValidationError, match=message):
        validate_project_manifest(raw)


def test_loader_is_bounded_and_requires_canonical_filename(tmp_path: Path) -> None:
    wrong_name = tmp_path / "manifest.yaml"
    wrong_name.write_text("schema_version: 1")
    with pytest.raises(ManifestValidationError, match="filename"):
        load_project_manifest(wrong_name)

    oversized = tmp_path / "project.yaml"
    oversized.write_bytes(b"x" * 1_000_001)
    with pytest.raises(ManifestValidationError, match="exceeds"):
        load_project_manifest(oversized)

    symlink = tmp_path / "nested" / "project.yaml"
    symlink.parent.mkdir()
    symlink.symlink_to(oversized)
    with pytest.raises(ManifestValidationError, match="non-symlink"):
        load_project_manifest(symlink)


def test_loader_rejects_duplicate_yaml_mapping_keys(tmp_path: Path) -> None:
    duplicate = tmp_path / "project.yaml"
    duplicate.write_text("schema_version: 1\nschema_version: 1\n")

    with pytest.raises(ManifestValidationError, match="duplicate YAML key"):
        load_project_manifest(duplicate)


def test_generated_payload_rejects_nonfinite_json_and_uppercase_hash() -> None:
    with pytest.raises(ManifestValidationError, match="canonical JSON"):
        generated_payload_hash({"score": float("nan")})

    raw = _manifest()
    generated = raw["generated"]
    assert isinstance(generated, dict)
    source_hash = generated["source_hash"]
    assert isinstance(source_hash, str)
    generated["source_hash"] = source_hash.upper()
    with pytest.raises(ManifestValidationError, match="lowercase SHA-256"):
        validate_project_manifest(raw)


@pytest.mark.parametrize(
    "remote",
    (
        "https://user:secret@example.invalid/repo.git",
        "https://token@example.invalid/repo.git",
        "https://example.invalid/repo.git?token=secret",
    ),
)
def test_manifest_rejects_repository_remote_credentials(remote: str) -> None:
    raw = _manifest()
    raw["human"]["repository"]["remote"] = remote  # type: ignore[index]

    with pytest.raises(ManifestValidationError, match="credentialless"):
        validate_project_manifest(raw)
