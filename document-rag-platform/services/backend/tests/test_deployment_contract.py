"""Static A11 deployment and container policy regression tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[4]
PLATFORM = REPO / "document-rag-platform"
BASE = PLATFORM / "compose.yaml"
DEV = PLATFORM / "compose.dev.yaml"
CI = PLATFORM / "compose.ci.yaml"
PROD = PLATFORM / "compose.prod.example.yaml"
DOCKERFILE = PLATFORM / "services/backend/Dockerfile"
ENV_EXAMPLE = PLATFORM / ".env.example"
CI_ENV = PLATFORM / "tests/deployment/compose-ci.env"
SMOKE_IMAGE = os.environ.get("A11_SMOKE_IMAGE")


def _compose(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert isinstance(payload.get("services"), dict)
    return payload


def _environment(service: dict) -> dict:
    environment = service.get("environment", {})
    assert isinstance(environment, dict)
    return environment


def test_compose_contract_files_exist() -> None:
    for path in (BASE, DEV, CI, PROD):
        assert path.is_file(), f"missing A11 compose contract: {path.name}"


def test_base_is_non_publishing_and_has_isolated_control_plane() -> None:
    payload = _compose(BASE)
    services = payload["services"]
    assert {
        "postgres",
        "redis",
        "minio",
        "minio-bootstrap",
        "migration",
        "backend",
        "worker",
        "scheduler",
    } <= set(services)
    for service in services.values():
        assert "ports" not in service
        for volume in service.get("volumes", []):
            assert not str(volume).startswith("./")
    assert {"backend", "data", "egress"} <= set(payload.get("networks", {}))
    assert services["backend"]["depends_on"]["migration"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["backend"]["depends_on"]["minio-bootstrap"]["condition"] == (
        "service_completed_successfully"
    )


def test_runtime_services_do_not_receive_bootstrap_credentials() -> None:
    services = _compose(BASE)["services"]
    for name in ("backend", "worker", "scheduler"):
        environment = _environment(services[name])
        rendered = "\n".join(f"{key}={value}" for key, value in environment.items())
        assert "POSTGRES_PASSWORD" not in rendered
        assert "MINIO_ROOT_USER" not in rendered
        assert "MINIO_ROOT_PASSWORD" not in rendered
        assert "MINIO_APP_ACCESS_KEY" in rendered
        assert "MINIO_APP_SECRET_KEY" in rendered
    assert (
        _environment(services["migration"])["DATABASE_URL"]
        != _environment(services["backend"])["DATABASE_URL"]
    )


def test_redis_is_authenticated_persistent_and_fail_closed() -> None:
    payload = _compose(BASE)
    redis = payload["services"]["redis"]
    command = " ".join(str(part) for part in redis["command"])
    assert "--requirepass" in command
    assert "--appendonly" in command
    assert "noeviction" in command
    assert any("redis_data" in str(volume) for volume in redis.get("volumes", []))
    assert "REDISCLI_AUTH" in " ".join(redis["healthcheck"]["test"])


def test_dev_overlay_preserves_source_mounts_and_loopback_ports() -> None:
    services = _compose(DEV)["services"]
    for name in ("backend", "worker", "scheduler"):
        assert "./services/backend:/app" in services[name]["volumes"]
        assert "backend_venv:/app/.venv" in services[name]["volumes"]
    for service in ("postgres", "redis", "minio", "backend"):
        for port in services[service].get("ports", []):
            assert str(port).startswith("127.0.0.1:")
    assert _compose(PLATFORM / "compose.override.yaml") == _compose(DEV)


def test_ci_overlay_is_ephemeral_and_has_no_external_provider() -> None:
    services = _compose(CI)["services"]
    assert not any("ports" in service for service in services.values())
    for name in ("backend", "worker"):
        environment = _environment(services[name])
        assert environment["LITELLM_BASE_URL"].endswith(".example.invalid/v1")
        assert environment["PROVIDER_REQUEST_RETENTION"] == "none"
    assert CI_ENV.is_file()


def test_production_overlay_hardens_writable_runtime_surface() -> None:
    services = _compose(PROD)["services"]
    for name in ("backend", "worker", "scheduler", "migration", "minio-bootstrap"):
        service = services[name]
        assert service["read_only"] is True
        assert service["tmpfs"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cap_drop"] == ["ALL"]
        assert service["mem_limit"]
        assert service["cpus"]
        assert not any(
            str(volume).startswith("./") for volume in service.get("volumes", [])
        )


def test_backend_image_is_multistage_non_root_and_self_probing() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    assert len(re.findall(r"^FROM ", source, flags=re.MULTILINE)) >= 2
    assert re.search(r"^USER (?!root\b).+", source, flags=re.MULTILINE)
    assert re.search(r"^HEALTHCHECK ", source, flags=re.MULTILINE)
    assert "uv sync --frozen --no-dev --no-install-project" in source
    assert "PYTHONDONTWRITEBYTECODE=1" in source
    assert "apk upgrade" not in source
    assert re.search(r"apk add --no-cache [^\n]*\bgit\b", source)


def test_bootstrap_rotation_is_bounded_and_scheduler_writes_only_to_tmpfs() -> None:
    services = _compose(BASE)["services"]
    bootstrap_service = services["minio-bootstrap"]
    bootstrap = "\n".join(bootstrap_service["command"])
    assert bootstrap_service["environment"]["MC_CONFIG_DIR"] == "/tmp/.mc"
    assert "MINIO_BOOTSTRAP_MAX_ATTEMPTS" in bootstrap
    assert "exit 1" in bootstrap
    assert "mc admin user info" not in bootstrap
    assert "mc admin user add local" in bootstrap
    scheduler_command = services["scheduler"]["command"]
    assert scheduler_command[-2:] == ["--schedule", "/tmp/celerybeat-schedule"]


@pytest.mark.parametrize(
    "invalid_attempts",
    [
        "",
        "0",
        "01",
        "-1",
        "abc",
        "1.5",
        "10.5",
        "12abc",
        "99x",
        "301",
        "999999999999999999999999999999999999999999",
    ],
)
def test_bootstrap_rejects_invalid_attempt_limit_before_calling_mc(
    invalid_attempts: str, tmp_path: Path
) -> None:
    services = _compose(BASE)["services"]
    bootstrap = "\n".join(services["minio-bootstrap"]["command"]).replace("$$", "$")
    marker = tmp_path / "mc-was-called"
    fake_mc = tmp_path / "mc"
    fake_mc.write_text(
        f"#!/bin/sh\ntouch {marker!s}\nexit 1\n",
        encoding="utf-8",
    )
    fake_mc.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "MINIO_BOOTSTRAP_MAX_ATTEMPTS": invalid_attempts,
        "MINIO_ROOT_USER": "root-user",
        "MINIO_ROOT_PASSWORD": "root-password",
        "MINIO_APP_ACCESS_KEY": "app-user",
        "MINIO_APP_SECRET_KEY": "app-password",
        "MINIO_BUCKET": "app-bucket",
    }
    result = subprocess.run(
        ["/bin/sh", "-ec", bootstrap],
        env=environment,
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert result.returncode != 0
    assert "must be an integer from 1 through 300" in result.stderr
    assert not marker.exists()


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI unavailable")
def test_resolved_compose_contracts() -> None:
    def resolve(*arguments: str) -> dict:
        result = subprocess.run(
            ["docker", "compose", *arguments, "config", "--format", "json"],
            cwd=PLATFORM,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    default = resolve("--env-file", str(ENV_EXAMPLE))
    assert default["services"]["backend"]["ports"][0]["host_ip"] == "127.0.0.1"

    ci = resolve(
        "--env-file",
        str(CI_ENV),
        "-f",
        str(BASE),
        "-f",
        str(CI),
    )
    assert not any("ports" in service for service in ci["services"].values())
    assert ci["services"]["backend"]["environment"]["LITELLM_BASE_URL"].endswith(
        ".example.invalid/v1"
    )

    prod = resolve(
        "--env-file",
        str(CI_ENV),
        "-f",
        str(BASE),
        "-f",
        str(PROD),
    )
    for name in ("backend", "worker", "scheduler", "migration"):
        service = prod["services"][name]
        assert "build" not in service
        assert re.fullmatch(r".+@sha256:[0-9a-f]{64}", service["image"])
        assert service["read_only"] is True
        assert len(service["tmpfs"]) == 1
        assert service["tmpfs"][0].startswith("/tmp:rw,")


@pytest.mark.skipif(
    not SMOKE_IMAGE, reason="set A11_SMOKE_IMAGE to inspect a built image"
)
def test_built_image_inspect_contract() -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", SMOKE_IMAGE, "--format", "{{json .Config}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)
    assert config["User"] == "10001:10001"
    assert config["Healthcheck"]["Test"][0] == "CMD"
    assert any(value == "PYTHONDONTWRITEBYTECODE=1" for value in config["Env"])
