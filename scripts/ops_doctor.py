#!/usr/bin/env python3
"""Read-only A11 deployment doctor with redacted machine receipts.

The command never starts, stops, removes or creates Docker resources. Names found
at runtime are represented by hashes so a receipt can be published without leaking
local project naming. ``--dry-run`` performs no subprocess or network activity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable
from urllib.parse import unquote, urlsplit


TOOL_VERSION = "1.1.0"
DEFAULT_EXPECTED_HEAD = "cv3_00000006"
REQUIRED_DEPENDENCIES = ("postgres", "redis", "minio")
OPTIONAL_LONG_RUNNING_SERVICES = ("backend", "worker")
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
MAX_COMPOSE_FILE_BYTES = 1024 * 1024
MAX_COMPOSE_FILES = 8
MAX_INVENTORY_ITEMS = 4096
INSPECT_BATCH_SIZE = 100
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
_SAFE_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_GIT_REVISION = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_PLANNED_CHECKS = [
    "resolve-compose-contract",
    "verify-local-docker-endpoint",
    "inspect-project-containers",
    "inspect-project-volumes",
    "compare-repository-and-database-migration-head",
    "probe-required-dependencies",
]


def _subprocess_env() -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "DOCKER_CONFIG",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


class OpsDoctorError(RuntimeError):
    """A bounded doctor contract could not be verified."""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _read_bounded(
    stream: BinaryIO,
    retained: bytearray,
    overflow: list[bool],
    maximum: int,
) -> None:
    """Drain a pipe while retaining at most ``maximum`` bytes in memory."""

    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return
            remaining = maximum - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow[0] = True
    except (OSError, ValueError):
        return


def _run_bounded(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 30,
    maximum: int = MAX_COMMAND_OUTPUT_BYTES,
) -> tuple[str, str]:
    """Run a read-only command with bounded stdout and stderr retention."""

    if maximum <= 0:
        raise ValueError("maximum must be positive")
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            env=_subprocess_env(),
        )
    except OSError as exc:
        raise OpsDoctorError("read-only dependency inspection failed") from exc
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    stdout_overflow = [False]
    stderr_overflow = [False]
    readers = [
        threading.Thread(
            target=_read_bounded,
            args=(process.stdout, stdout, stdout_overflow, maximum),
            daemon=True,
        ),
        threading.Thread(
            target=_read_bounded,
            args=(process.stderr, stderr, stderr_overflow, maximum),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    reader_stuck = False
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process(process)
        process.wait()
        raise OpsDoctorError("read-only dependency inspection timed out") from exc
    finally:
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            # A detached descendant can inherit a pipe after the command exits.
            # Kill the isolated process group so pipe draining cannot hang forever.
            _kill_process(process)
            for reader in readers:
                reader.join(timeout=1)
        reader_stuck = any(reader.is_alive() for reader in readers)
        process.stdout.close()
        process.stderr.close()
        for reader in readers:
            reader.join(timeout=1)
    if reader_stuck:
        raise OpsDoctorError("read-only dependency output did not terminate")
    if stdout_overflow[0] or stderr_overflow[0]:
        raise OpsDoctorError("read-only dependency output exceeded the safety limit")
    if return_code != 0:
        # Command output is deliberately not copied into the exception: Docker and
        # Compose diagnostics can contain interpolated credential values.
        raise OpsDoctorError("read-only dependency inspection failed")
    try:
        return stdout.decode("utf-8", errors="strict"), stderr.decode(
            "utf-8", errors="strict"
        )
    except UnicodeDecodeError as exc:
        raise OpsDoctorError("read-only dependency output is not UTF-8") from exc


def _run_text(argv: list[str], *, cwd: Path | None = None, timeout: int = 30) -> str:
    stdout, _ = _run_bounded(argv, cwd=cwd, timeout=timeout)
    return stdout


def _run_json(argv: list[str]) -> Any:
    try:
        return json.loads(_run_text(argv))
    except json.JSONDecodeError as exc:
        raise OpsDoctorError("read-only dependency returned invalid JSON") from exc


def _safe_string_list(value: Any, *, allowed: set[str] | None = None) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and _SAFE_NAME.fullmatch(item) for item in value)
        and len(value) == len(set(value))
        and (allowed is None or set(value) <= allowed)
    )


def _validate_receipt(payload: dict[str, Any]) -> None:
    """Validate the exact public receipt schema before any bytes are written."""

    base_keys = {
        "schema_version",
        "receipt_type",
        "tool_version",
        "observed_at_utc",
        "repository_revision",
        "project_name_hash",
        "planned_checks",
        "credential_values_retained",
        "effects_performed",
        "status",
    }
    full_keys = base_keys | {
        "compose_service_count",
        "compose_volume_count",
        "inventory",
        "dependencies",
        "database_check_performed",
        "dependency_container_identifiers_retained",
        "docker_context_name_hash",
        "docker_host_transport",
    }
    status_value = payload.get("status")
    expected_keys = base_keys if status_value == "DRY_RUN" else full_keys
    if set(payload) != expected_keys:
        raise OpsDoctorError("receipt does not match the public schema")
    if (
        payload.get("schema_version") != "1.0"
        or payload.get("receipt_type") != "ops-doctor"
        or payload.get("tool_version") != TOOL_VERSION
        or status_value not in {"PASS", "FAIL", "DRY_RUN"}
        or payload.get("planned_checks") != _PLANNED_CHECKS
        or payload.get("credential_values_retained") is not False
        or payload.get("effects_performed") is not False
        or not isinstance(payload.get("observed_at_utc"), str)
        or not _UTC_TIMESTAMP.fullmatch(payload["observed_at_utc"])
        or not isinstance(payload.get("project_name_hash"), str)
        or not _HEX_DIGEST.fullmatch(payload["project_name_hash"])
    ):
        raise OpsDoctorError("receipt contains an invalid public field")
    revision = payload.get("repository_revision")
    if status_value == "DRY_RUN":
        if revision is not None:
            raise OpsDoctorError("dry-run receipt retained a repository revision")
        return
    if not isinstance(revision, str) or not _GIT_REVISION.fullmatch(revision):
        raise OpsDoctorError("receipt contains an invalid repository revision")
    for count_key in ("compose_service_count", "compose_volume_count"):
        count = payload.get(count_key)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise OpsDoctorError("receipt contains an invalid count")
    if (
        not isinstance(payload.get("database_check_performed"), bool)
        or payload.get("dependency_container_identifiers_retained") is not False
        or payload.get("docker_host_transport") != "unix"
        or not isinstance(payload.get("docker_context_name_hash"), str)
        or not _HEX_DIGEST.fullmatch(payload["docker_context_name_hash"])
    ):
        raise OpsDoctorError("receipt contains invalid runtime metadata")
    inventory = payload.get("inventory")
    inventory_keys = {
        "orphan_container_count",
        "orphan_container_id_hashes",
        "orphan_volume_count",
        "orphan_volume_name_hashes",
    }
    if not isinstance(inventory, dict) or set(inventory) != inventory_keys:
        raise OpsDoctorError("receipt contains an invalid inventory summary")
    for count_key, hashes_key in (
        ("orphan_container_count", "orphan_container_id_hashes"),
        ("orphan_volume_count", "orphan_volume_name_hashes"),
    ):
        hashes = inventory.get(hashes_key)
        count = inventory.get(count_key)
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or not isinstance(hashes, list)
            or len(hashes) != count
            or not all(
                isinstance(item, str) and _HEX_DIGEST.fullmatch(item) for item in hashes
            )
        ):
            raise OpsDoctorError("receipt contains an invalid inventory count")
    dependencies = payload.get("dependencies")
    dependency_keys = {
        "status",
        "schema_at_head",
        "observed_head",
        "expected_head",
        "missing_dependencies",
        "missing_compose_services",
        "missing_container_services",
        "duplicate_container_services",
        "unready_container_services",
    }
    if not isinstance(dependencies, dict) or set(dependencies) != dependency_keys:
        raise OpsDoctorError("receipt contains an invalid dependency summary")
    known_dependencies = {
        "docker",
        *REQUIRED_DEPENDENCIES,
        *OPTIONAL_LONG_RUNNING_SERVICES,
    }
    if (
        dependencies.get("status") not in {"PASS", "FAIL"}
        or not isinstance(dependencies.get("schema_at_head"), bool)
        or not _safe_string_list(
            dependencies.get("missing_dependencies"), allowed=known_dependencies
        )
        or not _safe_string_list(
            dependencies.get("missing_compose_services"),
            allowed=set(REQUIRED_DEPENDENCIES),
        )
        or not _safe_string_list(
            dependencies.get("missing_container_services"),
            allowed=known_dependencies - {"docker"},
        )
        or not _safe_string_list(
            dependencies.get("duplicate_container_services"),
            allowed=known_dependencies - {"docker"},
        )
        or not _safe_string_list(
            dependencies.get("unready_container_services"),
            allowed=known_dependencies - {"docker"},
        )
    ):
        raise OpsDoctorError("receipt contains an invalid dependency field")
    for revision_key in ("observed_head", "expected_head"):
        value = dependencies.get(revision_key)
        if value is not None and (
            not isinstance(value, str) or not _SAFE_REVISION.fullmatch(value)
        ):
            raise OpsDoctorError("receipt contains an unsafe migration revision")


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    """Exclusively create one mode-0600 allowlisted JSON receipt."""

    _validate_receipt(payload)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise OpsDoctorError("receipt output must be a new regular file") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise OpsDoctorError("receipt output is not a private regular file")
    except Exception:
        path.unlink(missing_ok=True)
        raise


def analyze_compose_inventory(
    *,
    expected_services: set[str],
    expected_volumes: set[str],
    containers: list[dict[str, Any]],
    volumes: list[dict[str, str]],
    project: str,
) -> dict[str, Any]:
    orphan_containers = sorted(
        item["id"]
        for item in containers
        if item.get("project") == project
        and item.get("service") not in expected_services
    )
    orphan_volumes = sorted(
        item["name"]
        for item in volumes
        if item.get("project") == project and item.get("volume") not in expected_volumes
    )
    return {
        "orphan_container_count": len(orphan_containers),
        "orphan_container_id_hashes": [sha256_text(item) for item in orphan_containers],
        "orphan_volume_count": len(orphan_volumes),
        "orphan_volume_name_hashes": [sha256_text(item) for item in orphan_volumes],
    }


def evaluate_dependencies(
    *,
    expected_head: str,
    observed_head: str | None,
    dependency_status: dict[str, bool],
    missing_compose_services: list[str] | None = None,
    missing_container_services: list[str] | None = None,
    duplicate_container_services: list[str] | None = None,
    unready_container_services: list[str] | None = None,
) -> dict[str, Any]:
    missing = sorted(
        name for name, available in dependency_status.items() if not available
    )
    missing_compose = sorted(missing_compose_services or [])
    missing_containers = sorted(missing_container_services or [])
    duplicate_containers = sorted(duplicate_container_services or [])
    unready_containers = sorted(unready_container_services or [])
    schema_at_head = observed_head == expected_head
    passed = (
        schema_at_head
        and not missing
        and not missing_compose
        and not missing_containers
        and not duplicate_containers
        and not unready_containers
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "schema_at_head": schema_at_head,
        "observed_head": observed_head,
        "expected_head": expected_head,
        "missing_dependencies": missing,
        "missing_compose_services": missing_compose,
        "missing_container_services": missing_containers,
        "duplicate_container_services": duplicate_containers,
        "unready_container_services": unready_containers,
    }


def _parse_name_projection(output: str, *, kind: str) -> set[str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) > MAX_INVENTORY_ITEMS:
        raise OpsDoctorError(f"{kind} projection exceeded the item limit")
    if any(not _SAFE_NAME.fullmatch(line) for line in lines):
        raise OpsDoctorError(f"{kind} projection contains an invalid name")
    if len(lines) != len(set(lines)):
        raise OpsDoctorError(f"{kind} projection contains duplicate names")
    return set(lines)


def _validate_inputs(
    repo: Path, compose_files: list[Path], project: str, expected_head: str
) -> tuple[Path, list[Path]]:
    if not _SAFE_PROJECT.fullmatch(project):
        raise OpsDoctorError("project name is invalid")
    if not _SAFE_REVISION.fullmatch(expected_head):
        raise OpsDoctorError("expected migration head is invalid")
    try:
        repo_root = repo.resolve(strict=True)
    except OSError as exc:
        raise OpsDoctorError("repository root is unavailable") from exc
    if not repo_root.is_dir():
        raise OpsDoctorError("repository root is not a directory")
    if not compose_files:
        raise OpsDoctorError("at least one Compose file is required")
    if len(compose_files) > MAX_COMPOSE_FILES:
        raise OpsDoctorError("Compose file count exceeded the safety limit")
    validated: list[Path] = []
    for supplied in compose_files:
        candidate = supplied if supplied.is_absolute() else repo_root / supplied
        try:
            metadata = candidate.lstat()
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(repo_root)
        except (OSError, ValueError) as exc:
            raise OpsDoctorError(
                "Compose files must be regular files within the repository"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode) or supplied.suffix.lower() not in {
            ".yaml",
            ".yml",
        }:
            raise OpsDoctorError(
                "Compose files must be regular YAML files within the repository"
            )
        if metadata.st_size > MAX_COMPOSE_FILE_BYTES:
            raise OpsDoctorError("Compose file exceeded the safety limit")
        validated.append(resolved)
    if len(validated) != len(set(validated)):
        raise OpsDoctorError("Compose file list contains duplicates")
    return repo_root, validated


def _validate_local_unix_host(host: str) -> None:
    parsed = urlsplit(host)
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme != "unix"
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not decoded_path.startswith("/")
        or "\x00" in decoded_path
        or any(part == ".." for part in decoded_path.split("/"))
    ):
        raise OpsDoctorError("Docker must use a local Unix-socket endpoint")


def _ensure_local_docker() -> str:
    if shutil.which("docker") is None:
        raise OpsDoctorError("Docker CLI is unavailable")
    if os.environ.get("DOCKER_TLS_VERIFY") or os.environ.get("DOCKER_CERT_PATH"):
        raise OpsDoctorError("remote Docker TLS configuration is not allowed")
    configured_host = os.environ.get("DOCKER_HOST")
    if configured_host:
        _validate_local_unix_host(configured_host)
    context = _run_text(["docker", "context", "show"]).strip()
    if not context or not _SAFE_NAME.fullmatch(context):
        raise OpsDoctorError("Docker context name is invalid")
    rendered_host = _run_text(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            "{{json .Endpoints.docker.Host}}",
        ]
    ).strip()
    try:
        context_host = json.loads(rendered_host)
    except json.JSONDecodeError as exc:
        raise OpsDoctorError("Docker context endpoint is invalid") from exc
    if not isinstance(context_host, str):
        raise OpsDoctorError("Docker context endpoint is unavailable")
    _validate_local_unix_host(context_host)
    return context


def _parse_projection_line(line: str, *, fields: int) -> list[Any]:
    parts = line.split("\t")
    if len(parts) != fields:
        raise OpsDoctorError("Docker inventory projection is malformed")
    try:
        decoded = [json.loads(part) for part in parts]
    except json.JSONDecodeError as exc:
        raise OpsDoctorError("Docker inventory projection is malformed") from exc
    return decoded


def _inspect_container_batch(ids: list[str], project: str) -> list[dict[str, Any]]:
    projection = (
        '{{json .Id}}\t{{json (index .Config.Labels "com.docker.compose.project")}}'
        '\t{{json (index .Config.Labels "com.docker.compose.service")}}'
        '\t{{json (index .State "Status")}}'
        '\t{{with (index .State "Health")}}'
        '{{json (index . "Status")}}{{else}}null{{end}}'
    )
    output = _run_text(["docker", "inspect", "--format", projection, *ids])
    result: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        identifier, item_project, service, state, health = _parse_projection_line(
            line, fields=5
        )
        if (
            not all(
                isinstance(value, str)
                for value in (identifier, item_project, service, state)
            )
            or not _CONTAINER_ID.fullmatch(identifier)
            or item_project != project
            or not _SAFE_NAME.fullmatch(service)
            or state
            not in {
                "created",
                "restarting",
                "running",
                "removing",
                "paused",
                "exited",
                "dead",
            }
            or health not in {None, "healthy", "unhealthy", "starting"}
        ):
            raise OpsDoctorError("Docker container labels did not match the project")
        result.append(
            {
                "id": identifier,
                "project": item_project,
                "service": service,
                "state": state,
                "health": health,
            }
        )
    if len(result) != len(ids) or {item["id"] for item in result} != set(ids):
        raise OpsDoctorError("Docker container inventory changed during inspection")
    return result


def _inspect_volume_batch(names: list[str], project: str) -> list[dict[str, str]]:
    projection = (
        '{{json .Name}}\t{{json (index .Labels "com.docker.compose.project")}}'
        '\t{{json (index .Labels "com.docker.compose.volume")}}'
    )
    output = _run_text(["docker", "volume", "inspect", "--format", projection, *names])
    result: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        name, item_project, volume = _parse_projection_line(line, fields=3)
        if (
            not all(isinstance(value, str) for value in (name, item_project, volume))
            or not _SAFE_NAME.fullmatch(name)
            or item_project != project
            or not _SAFE_NAME.fullmatch(volume)
        ):
            raise OpsDoctorError("Docker volume labels did not match the project")
        result.append({"name": name, "project": item_project, "volume": volume})
    if len(result) != len(names) or {item["name"] for item in result} != set(names):
        raise OpsDoctorError("Docker volume inventory changed during inspection")
    return result


def _inventory_names(
    argv: list[str], pattern: re.Pattern[str], *, kind: str
) -> list[str]:
    names = _run_text(argv).split()
    if len(names) > MAX_INVENTORY_ITEMS:
        raise OpsDoctorError(f"Docker {kind} inventory exceeded the item limit")
    if len(names) != len(set(names)) or any(
        not pattern.fullmatch(name) for name in names
    ):
        raise OpsDoctorError(f"Docker {kind} inventory is malformed")
    return names


def _docker_inventory(
    project: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    container_ids = _inventory_names(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        _CONTAINER_ID,
        kind="container",
    )
    volume_names = _inventory_names(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        _SAFE_NAME,
        kind="volume",
    )
    containers: list[dict[str, Any]] = []
    for start in range(0, len(container_ids), INSPECT_BATCH_SIZE):
        containers.extend(
            _inspect_container_batch(
                container_ids[start : start + INSPECT_BATCH_SIZE], project
            )
        )
    volumes: list[dict[str, str]] = []
    for start in range(0, len(volume_names), INSPECT_BATCH_SIZE):
        volumes.extend(
            _inspect_volume_batch(
                volume_names[start : start + INSPECT_BATCH_SIZE], project
            )
        )
    return containers, volumes


def _contract_services(expected_services: set[str]) -> tuple[str, ...]:
    return (
        *REQUIRED_DEPENDENCIES,
        *(
            service
            for service in OPTIONAL_LONG_RUNNING_SERVICES
            if service in expected_services
        ),
    )


def _resolve_service_containers(
    containers: list[dict[str, Any]], project: str, services: tuple[str, ...]
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    resolved: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    duplicates: list[str] = []
    for service in services:
        matches = [
            item
            for item in containers
            if item.get("project") == project and item.get("service") == service
        ]
        if not matches:
            missing.append(service)
        elif len(matches) > 1:
            duplicates.append(service)
        else:
            resolved[service] = matches[0]
    return resolved, missing, duplicates


def _resolve_dependency_containers(
    containers: list[dict[str, Any]], project: str
) -> tuple[dict[str, str], list[str], list[str]]:
    """Compatibility wrapper for the dependency-only resolution contract."""

    resolved, missing, duplicates = _resolve_service_containers(
        containers, project, REQUIRED_DEPENDENCIES
    )
    return (
        {service: str(item["id"]) for service, item in resolved.items()},
        missing,
        duplicates,
    )


def _unready_services(resolved: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        service
        for service, item in resolved.items()
        if item.get("state") != "running" or item.get("health") not in {None, "healthy"}
    )


def _database_head(database_url: str) -> str:
    try:
        import psycopg2

        connection = psycopg2.connect(database_url, connect_timeout=5)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version")
                row = cursor.fetchone()
        finally:
            connection.close()
    except Exception as exc:
        raise OpsDoctorError("database schema inspection failed") from exc
    if not row or not isinstance(row[0], str) or not _SAFE_REVISION.fullmatch(row[0]):
        raise OpsDoctorError("database migration head is unavailable")
    return row[0]


def _container_probe(container: str, probe: str) -> bool:
    commands = {
        "postgres": ["pg_isready"],
        "redis": [
            "/bin/sh",
            "-ec",
            'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping | grep -q PONG',
        ],
        "minio": [
            "/bin/sh",
            "-ec",
            "curl -fsS http://127.0.0.1:9000/minio/health/ready >/dev/null",
        ],
    }
    try:
        _run_bounded(
            ["docker", "exec", container, *commands[probe]],
            timeout=10,
            maximum=16 * 1024,
        )
        return True
    except OpsDoctorError:
        return False


def doctor(
    *,
    repo: Path,
    compose_files: list[Path],
    project: str,
    expected_head: str,
    dry_run: bool,
    command_runner: Callable[[list[str]], Any] | None = None,
    database_url: str | None = None,
    dependency_containers: dict[str, str] | None = None,
) -> dict[str, Any]:
    repo_root, validated_compose_files = _validate_inputs(
        repo, compose_files, project, expected_head
    )
    base = {
        "schema_version": "1.0",
        "receipt_type": "ops-doctor",
        "tool_version": TOOL_VERSION,
        "observed_at_utc": _utc_now(),
        "repository_revision": None,
        "project_name_hash": sha256_text(project),
        "planned_checks": list(_PLANNED_CHECKS),
        "credential_values_retained": False,
        "effects_performed": False,
    }
    if dry_run:
        return {**base, "status": "DRY_RUN"}
    if command_runner is not None:
        raise OpsDoctorError("custom command runner is accepted only by dry-run tests")
    if dependency_containers and any(dependency_containers.values()):
        raise OpsDoctorError(
            "dependency containers must be resolved from Docker labels"
        )
    revision = _run_text(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
    if not _GIT_REVISION.fullmatch(revision):
        raise OpsDoctorError("repository revision is invalid")
    base["repository_revision"] = revision
    docker_context = _ensure_local_docker()
    compose_command = ["docker", "compose", "--project-name", project]
    for path in validated_compose_files:
        compose_command.extend(["-f", str(path)])
    expected_services = _parse_name_projection(
        _run_text([*compose_command, "config", "--no-interpolate", "--services"]),
        kind="service",
    )
    expected_volumes = _parse_name_projection(
        _run_text([*compose_command, "config", "--no-interpolate", "--volumes"]),
        kind="volume",
    )
    missing_compose_services = sorted(set(REQUIRED_DEPENDENCIES) - expected_services)
    containers, volumes = _docker_inventory(project)
    inventory = analyze_compose_inventory(
        expected_services=expected_services,
        expected_volumes=expected_volumes,
        containers=containers,
        volumes=volumes,
        project=project,
    )
    contract_services = _contract_services(expected_services)
    resolved_services, missing_containers, duplicate_containers = (
        _resolve_service_containers(containers, project, contract_services)
    )
    unready_containers = _unready_services(resolved_services)
    observed_head = _database_head(database_url) if database_url else None
    dependency_status = {"docker": True}
    for name in REQUIRED_DEPENDENCIES:
        container = resolved_services.get(name)
        dependency_status[name] = (
            name in expected_services
            and container is not None
            and name not in unready_containers
            and _container_probe(str(container["id"]), name)
        )
    dependencies = evaluate_dependencies(
        expected_head=expected_head,
        observed_head=observed_head,
        dependency_status=dependency_status,
        missing_compose_services=missing_compose_services,
        missing_container_services=missing_containers,
        duplicate_container_services=duplicate_containers,
        unready_container_services=unready_containers,
    )
    status = (
        "PASS"
        if dependencies["status"] == "PASS"
        and inventory["orphan_container_count"] == 0
        and inventory["orphan_volume_count"] == 0
        else "FAIL"
    )
    return {
        **base,
        "status": status,
        "compose_service_count": len(expected_services),
        "compose_volume_count": len(expected_volumes),
        "inventory": inventory,
        "dependencies": dependencies,
        "database_check_performed": database_url is not None,
        "dependency_container_identifiers_retained": False,
        "docker_context_name_hash": sha256_text(docker_context),
        "docker_host_transport": "unix",
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--compose-file", type=Path, action="append", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--expected-head", default=DEFAULT_EXPECTED_HEAD)
    parser.add_argument("--database-url-env")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        database_url = None
        if args.database_url_env:
            database_url = os.environ.get(args.database_url_env)
            if not database_url:
                raise OpsDoctorError("named database credential is unavailable")
        result = doctor(
            repo=args.repo,
            compose_files=args.compose_file,
            project=args.project_name,
            expected_head=args.expected_head,
            dry_run=args.dry_run,
            database_url=database_url,
        )
        write_receipt(args.json_output, result)
        return 0 if result["status"] in {"PASS", "DRY_RUN"} else 2
    except OpsDoctorError as exc:
        print(f"ops doctor failed closed: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("ops doctor failed closed: internal error", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
