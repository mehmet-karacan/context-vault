"""Static A11 deployment and container policy regression tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg2
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
POSTGRES_INIT = PLATFORM / "infra/docker/postgres/10-runtime-role.sql"
RUNTIME_GRANTS = PLATFORM / "infra/docker/postgres/apply_runtime_grants.py"
PROVIDER_CA_GUARD = PLATFORM / "infra/docker/provider/require-provider-ca.sh"
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
    for name in ("backend", "worker", "scheduler"):
        assert _environment(services[name])["EXPECTED_DATABASE_ROLE"].startswith(
            "${POSTGRES_RUNTIME_USER:"
        )
        assert "METRICS_EXPORT_MODE" in _environment(services[name])
        assert "METRICS_STATSD_HOST" in _environment(services[name])
        assert "METRICS_STATSD_PORT" in _environment(services[name])


def test_database_role_bootstrap_and_post_migration_grant_contract() -> None:
    payload = _compose(BASE)
    services = payload["services"]
    postgres = services["postgres"]
    migration = services["migration"]
    assert POSTGRES_INIT.is_file()
    assert RUNTIME_GRANTS.is_file()
    assert "POSTGRES_RUNTIME_USER" in _environment(postgres)
    assert "POSTGRES_RUNTIME_PASSWORD" in _environment(postgres)
    assert "POSTGRES_RUNTIME_USER" in _environment(migration)
    assert migration["command"][-1].endswith("apply_runtime_grants.py")
    assert migration["command"][2].startswith("python -m alembic upgrade head &&")
    assert services["backend"]["depends_on"]["migration"]["condition"] == (
        "service_completed_successfully"
    )
    init_mount = postgres["configs"][0]
    grant_mount = migration["configs"][0]
    assert init_mount == {
        "source": "postgres_runtime_role_init",
        "target": "/docker-entrypoint-initdb.d/10-runtime-role.sql",
        "mode": 0o444,
    }
    assert grant_mount == {
        "source": "postgres_runtime_grants",
        "target": "/opt/context-vault/apply_runtime_grants.py",
        "mode": 0o444,
    }
    init_source = POSTGRES_INIT.read_text(encoding="utf-8")
    grants_source = RUNTIME_GRANTS.read_text(encoding="utf-8")
    for forbidden_capability in (
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert forbidden_capability in init_source
    assert "REVOKE CREATE ON SCHEMA" in init_source
    assert "REVOKE TEMPORARY ON DATABASE" in init_source
    assert "ALTER DEFAULT PRIVILEGES" in init_source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in grants_source
    assert "GRANT USAGE, SELECT ON ALL SEQUENCES" in grants_source
    assert "GRANT EXECUTE ON ALL FUNCTIONS" in grants_source
    assert "pg_auth_members" in grants_source
    assert "runtime database role owns application objects" in grants_source


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


def test_production_provider_ca_is_external_read_only_and_fail_closed() -> None:
    payload = _compose(PROD)
    assert PROVIDER_CA_GUARD.is_file()
    assert payload["secrets"]["provider_ca_bundle"]["external"] is True
    assert "file" not in payload["secrets"]["provider_ca_bundle"]
    for name in ("backend", "worker"):
        service = payload["services"][name]
        assert service["entrypoint"] == [
            "/bin/sh",
            "/opt/context-vault/require-provider-ca.sh",
        ]
        assert service["environment"]["SSL_CERT_FILE"] == (
            "/run/secrets/provider_ca_bundle"
        )
        assert service["environment"]["REQUESTS_CA_BUNDLE"] == (
            "/run/secrets/provider_ca_bundle"
        )
        assert service["secrets"] == [
            {
                "source": "provider_ca_bundle",
                "target": "provider_ca_bundle",
                "mode": 0o444,
            }
        ]
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "provider_ca_bundle" not in dockerfile
    assert "COPY" not in PROVIDER_CA_GUARD.read_text(encoding="utf-8")
    scheduler_environment = payload["services"]["scheduler"]["environment"]
    assert scheduler_environment["LITELLM_BASE_URL"] == (
        "https://provider-disabled.example.invalid/v1"
    )
    assert scheduler_environment["LITELLM_API_KEY"] == (
        "provider-disabled-for-scheduler"
    )
    assert payload["volumes"]["operational_receipts"]["external"] is True
    worker = payload["services"]["worker"]
    assert worker["environment"]["OPERATIONAL_BACKUP_RECEIPT_PATH"].endswith(
        "/BACKUP_RECEIPT.json"
    )
    assert worker["environment"]["OPERATIONAL_RESTORE_RECEIPT_PATH"].endswith(
        "/RESTORE_RECEIPT.json"
    )
    assert worker["volumes"] == ["operational_receipts:/run/context-vault/receipts:ro"]


def test_provider_ca_guard_rejects_missing_mismatch_and_invalid_bundle(
    tmp_path: Path,
) -> None:
    valid_ca = tmp_path / "provider-ca.pem"
    shutil.copyfile(ssl.get_default_verify_paths().cafile, valid_ca)
    invalid_ca = tmp_path / "invalid-ca.pem"
    invalid_ca.write_text("not a certificate\n", encoding="utf-8")

    def invoke(ca_path: Path, requests_path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/sh", str(PROVIDER_CA_GUARD), "/bin/sh", "-c", "exit 0"],
            env={
                **os.environ,
                "SSL_CERT_FILE": str(ca_path),
                "REQUESTS_CA_BUNDLE": str(requests_path),
            },
            capture_output=True,
            text=True,
            timeout=5,
        )

    assert invoke(valid_ca, valid_ca).returncode == 0
    assert invoke(tmp_path / "missing.pem", tmp_path / "missing.pem").returncode != 0
    assert invoke(valid_ca, invalid_ca).returncode != 0
    assert invoke(invalid_ca, invalid_ca).returncode != 0


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
    for name in ("backend", "worker", "scheduler"):
        assert prod["services"][name]["environment"]["EXPECTED_DATABASE_ROLE"] == (
            "ci_runtime"
        )


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


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("A11_RUN_DISPOSABLE_DB_TEST") != "1",
    reason="set A11_RUN_DISPOSABLE_DB_TEST=1 for isolated PostgreSQL privilege drill",
)
def test_disposable_postgres_runtime_role_can_use_app_objects_but_not_ddl() -> None:
    """Prove real grants and denials in unique, disposable Docker resources."""
    token = uuid.uuid4().hex[:12]
    container = f"cv-a11-role-{token}"
    volume = f"cv-a11-role-{token}-data"
    database = "context_vault_role_test"
    admin_role = "cv_test_owner"
    runtime_role = "cv_test_runtime"
    admin_password = "admin-only-password-01234567890123456789"
    runtime_password = "runtime-only-password-012345678901234567"
    image = (
        "pgvector/pgvector:pg16@sha256:"
        "ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b"
    )
    _docker("volume", "create", "--label", f"com.context-vault.test={token}", volume)
    try:
        _docker(
            "run",
            "--detach",
            "--name",
            container,
            "--label",
            f"com.context-vault.test={token}",
            "--mount",
            f"source={volume},target=/var/lib/postgresql/data",
            "--mount",
            f"type=bind,source={POSTGRES_INIT},target=/docker-entrypoint-initdb.d/10-runtime-role.sql,readonly",
            "--env",
            f"POSTGRES_DB={database}",
            "--env",
            f"POSTGRES_USER={admin_role}",
            "--env",
            f"POSTGRES_PASSWORD={admin_password}",
            "--env",
            f"POSTGRES_RUNTIME_USER={runtime_role}",
            "--env",
            f"POSTGRES_RUNTIME_PASSWORD={runtime_password}",
            "--env",
            "DATABASE_SCHEMA=public",
            "--publish",
            "127.0.0.1::5432",
            image,
        )
        published = _docker("port", container, "5432/tcp").stdout.strip()
        host_port = int(published.rsplit(":", 1)[1])
        for _ in range(60):
            try:
                probe = psycopg2.connect(
                    host="127.0.0.1",
                    port=host_port,
                    dbname=database,
                    user=admin_role,
                    password=admin_password,
                    connect_timeout=1,
                )
            except psycopg2.OperationalError:
                time.sleep(0.25)
            else:
                probe.close()
                break
        else:
            pytest.fail("disposable PostgreSQL did not become ready")

        database_logs = _docker("logs", container).stdout
        assert admin_password not in database_logs
        assert runtime_password not in database_logs

        admin_url = (
            f"postgresql://{admin_role}:{admin_password}@127.0.0.1:"
            f"{host_port}/{database}"
        )
        runtime_url = (
            f"postgresql://{runtime_role}:{runtime_password}@127.0.0.1:"
            f"{host_port}/{database}"
        )

        # NOINHERIT alone does not prevent SET ROLE. The grant verifier must
        # reject any retained-cluster membership before declaring PASS.
        with psycopg2.connect(admin_url) as connection, connection.cursor() as cursor:
            cursor.execute("CREATE ROLE cv_test_escalation CREATEDB NOLOGIN")
            cursor.execute(
                f"GRANT cv_test_escalation TO {runtime_role}"  # test-only safe ids
            )
        membership_probe = subprocess.run(
            [sys.executable, str(RUNTIME_GRANTS)],
            env={
                **os.environ,
                "DATABASE_URL": admin_url,
                "DATABASE_SCHEMA": "public",
                "POSTGRES_RUNTIME_USER": runtime_role,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert membership_probe.returncode == 1
        assert "has role memberships" in membership_probe.stderr
        with psycopg2.connect(admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"REVOKE cv_test_escalation FROM {runtime_role}"  # test-only safe ids
            )
            cursor.execute("DROP ROLE cv_test_escalation")

        with psycopg2.connect(admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE runtime_probe "
                "(id bigserial PRIMARY KEY, value text NOT NULL)"
            )
            cursor.execute(
                "CREATE FUNCTION runtime_echo(integer) RETURNS integer "
                "LANGUAGE SQL IMMUTABLE AS 'SELECT $1'"
            )
            cursor.execute(f"ALTER TABLE runtime_probe OWNER TO {runtime_role}")

        ownership_probe = subprocess.run(
            [sys.executable, str(RUNTIME_GRANTS)],
            env={
                **os.environ,
                "DATABASE_URL": admin_url,
                "DATABASE_SCHEMA": "public",
                "POSTGRES_RUNTIME_USER": runtime_role,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert ownership_probe.returncode == 1
        assert "owns database objects" in ownership_probe.stderr
        with psycopg2.connect(admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(f"ALTER TABLE runtime_probe OWNER TO {admin_role}")
            cursor.execute("CREATE DOMAIN runtime_owned_domain AS text")
            cursor.execute(f"ALTER DOMAIN runtime_owned_domain OWNER TO {runtime_role}")

        type_ownership_probe = subprocess.run(
            [sys.executable, str(RUNTIME_GRANTS)],
            env={
                **os.environ,
                "DATABASE_URL": admin_url,
                "DATABASE_SCHEMA": "public",
                "POSTGRES_RUNTIME_USER": runtime_role,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert type_ownership_probe.returncode == 1
        assert "owns database objects" in type_ownership_probe.stderr
        with psycopg2.connect(admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(f"ALTER DOMAIN runtime_owned_domain OWNER TO {admin_role}")
            cursor.execute("DROP DOMAIN runtime_owned_domain")
            cursor.execute(
                "CREATE COLLATION runtime_owned_collation "
                "(provider = libc, locale = 'C')"
            )
            cursor.execute(
                f"ALTER COLLATION runtime_owned_collation OWNER TO {runtime_role}"
            )

        generic_ownership_probe = subprocess.run(
            [sys.executable, str(RUNTIME_GRANTS)],
            env={
                **os.environ,
                "DATABASE_URL": admin_url,
                "DATABASE_SCHEMA": "public",
                "POSTGRES_RUNTIME_USER": runtime_role,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert generic_ownership_probe.returncode == 1
        assert "owns database objects" in generic_ownership_probe.stderr
        with psycopg2.connect(admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"ALTER COLLATION runtime_owned_collation OWNER TO {admin_role}"
            )
            cursor.execute("DROP COLLATION runtime_owned_collation")

        grants = subprocess.run(
            [sys.executable, str(RUNTIME_GRANTS)],
            env={
                **os.environ,
                "DATABASE_URL": admin_url,
                "DATABASE_SCHEMA": "public",
                "POSTGRES_RUNTIME_USER": runtime_role,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert grants.returncode == 0, grants.stderr
        assert grants.stdout.strip() == "runtime database grants: PASS"
        assert admin_password not in grants.stdout + grants.stderr
        assert runtime_password not in grants.stdout + grants.stderr

        # Objects created by later migrations inherit the same bounded grants.
        with psycopg2.connect(admin_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE runtime_probe_late "
                "(id bigserial PRIMARY KEY, value text NOT NULL)"
            )
            cursor.execute(
                "CREATE FUNCTION runtime_echo_late(integer) RETURNS integer "
                "LANGUAGE SQL IMMUTABLE AS 'SELECT $1'"
            )

        with psycopg2.connect(runtime_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO runtime_probe(value) VALUES ('synthetic') RETURNING id"
            )
            first_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO runtime_probe_late(value) VALUES ('synthetic-late')"
            )
            cursor.execute(
                "UPDATE runtime_probe SET value = 'updated' WHERE id = %s", (first_id,)
            )
            cursor.execute(
                "SELECT value, runtime_echo(7), runtime_echo_late(8) "
                "FROM runtime_probe WHERE id = %s",
                (first_id,),
            )
            assert cursor.fetchone() == ("updated", 7, 8)
            cursor.execute("DELETE FROM runtime_probe WHERE id = %s", (first_id,))
            cursor.execute(
                "SELECT rolsuper, rolcreaterole, rolcreatedb, rolinherit, rolreplication, "
                "rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            assert cursor.fetchone() == (False, False, False, False, False, False)

        forbidden = (
            "CREATE TABLE runtime_forbidden(id integer)",
            "CREATE TEMP TABLE runtime_temp_forbidden(id integer)",
            "ALTER TABLE runtime_probe ADD COLUMN forbidden integer",
            "DROP TABLE runtime_probe",
            "CREATE SCHEMA runtime_forbidden",
            "CREATE ROLE runtime_forbidden",
        )
        for statement in forbidden:
            with (
                psycopg2.connect(runtime_url) as connection,
                connection.cursor() as cursor,
            ):
                with pytest.raises(psycopg2.Error) as denied:
                    cursor.execute(statement)
                assert denied.value.pgcode == "42501"
                connection.rollback()
    finally:
        _docker("rm", "--force", "--volumes", container, check=False)
        _docker("volume", "rm", "--force", volume, check=False)
        remaining_containers = _docker(
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=com.context-vault.test={token}",
        ).stdout.strip()
        remaining_volumes = _docker(
            "volume",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.context-vault.test={token}",
        ).stdout.strip()
        assert remaining_containers == ""
        assert remaining_volumes == ""
