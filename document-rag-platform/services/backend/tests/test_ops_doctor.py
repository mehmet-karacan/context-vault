"""A11 data-safe operational doctor contract tests."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts/ops_doctor.py"
COMPOSE_FILE = REPO / "document-rag-platform/compose.yaml"


def _module():
    spec = importlib.util.spec_from_file_location("ops_doctor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_finds_only_project_scoped_orphans() -> None:
    module = _module()
    result = module.analyze_compose_inventory(
        expected_services={"backend", "postgres"},
        expected_volumes={"postgres_data"},
        containers=[
            {"project": "context-vault", "service": "backend", "id": "a" * 64},
            {
                "project": "context-vault",
                "service": "old-worker",
                "id": "b" * 64,
            },
            {"project": "unrelated", "service": "old-worker", "id": "c" * 64},
        ],
        volumes=[
            {"project": "context-vault", "volume": "postgres_data", "name": "kept"},
            {"project": "context-vault", "volume": "old_data", "name": "orphan"},
            {"project": "unrelated", "volume": "old_data", "name": "foreign"},
        ],
        project="context-vault",
    )
    assert result["orphan_container_count"] == 1
    assert result["orphan_volume_count"] == 1
    assert result["orphan_container_id_hashes"] == [module.sha256_text("b" * 64)]
    assert result["orphan_volume_name_hashes"] == [module.sha256_text("orphan")]
    assert "old-worker" not in json.dumps(result)


def test_dry_run_has_no_command_effect(tmp_path: Path) -> None:
    module = _module()
    calls: list[list[str]] = []
    result = module.doctor(
        repo=REPO,
        compose_files=[COMPOSE_FILE],
        project="context-vault",
        expected_head="cv3_00000006",
        dry_run=True,
        command_runner=lambda argv: calls.append(argv),
    )
    assert calls == []
    assert result["status"] == "DRY_RUN"
    assert result["effects_performed"] is False
    assert result["planned_checks"]
    assert result["repository_revision"] is None

    target = tmp_path / "doctor.json"
    module.write_receipt(target, result)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_receipt_is_exclusive_and_exact_allowlist(tmp_path: Path) -> None:
    module = _module()
    payload = module.doctor(
        repo=REPO,
        compose_files=[COMPOSE_FILE],
        project="context-vault",
        expected_head="cv3_00000006",
        dry_run=True,
    )
    target = tmp_path / "doctor.json"
    module.write_receipt(target, payload)
    with pytest.raises(module.OpsDoctorError):
        module.write_receipt(target, payload)

    unsafe = dict(payload)
    unsafe["database_url"] = "postgresql://user:secret@db/x"
    with pytest.raises(module.OpsDoctorError):
        module.write_receipt(tmp_path / "unsafe.json", unsafe)
    assert not (tmp_path / "unsafe.json").exists()

    invalid_nested = dict(payload)
    invalid_nested["planned_checks"] = [*payload["planned_checks"], "PASSWORD=hunter2"]
    with pytest.raises(module.OpsDoctorError):
        module.write_receipt(tmp_path / "nested.json", invalid_nested)


def test_schema_and_dependency_summary_fails_closed() -> None:
    module = _module()
    result = module.evaluate_dependencies(
        expected_head="cv3_00000006",
        observed_head="cv3_00000005",
        dependency_status={"postgres": True, "redis": True, "minio": False},
        missing_container_services=["minio"],
        duplicate_container_services=["redis"],
    )
    assert result["status"] == "FAIL"
    assert result["schema_at_head"] is False
    assert result["missing_dependencies"] == ["minio"]
    assert result["missing_container_services"] == ["minio"]
    assert result["duplicate_container_services"] == ["redis"]


def test_missing_or_duplicate_compose_services_fail_closed() -> None:
    module = _module()
    with pytest.raises(module.OpsDoctorError, match="duplicate"):
        module._parse_name_projection("postgres\nredis\nredis\nminio\n", kind="service")

    result = module.evaluate_dependencies(
        expected_head="cv3_00000006",
        observed_head="cv3_00000006",
        dependency_status={"postgres": True, "redis": True, "minio": True},
        missing_compose_services=["minio"],
    )
    assert result["status"] == "FAIL"
    assert result["missing_compose_services"] == ["minio"]


def test_dependency_containers_require_one_exact_project_service_match() -> None:
    module = _module()
    containers = [
        {"project": "context-vault", "service": "postgres", "id": "a" * 64},
        {"project": "context-vault", "service": "redis", "id": "b" * 64},
        {"project": "context-vault", "service": "redis", "id": "c" * 64},
        {"project": "other", "service": "minio", "id": "d" * 64},
    ]
    resolved, missing, duplicates = module._resolve_dependency_containers(
        containers, "context-vault"
    )
    assert resolved == {"postgres": "a" * 64}
    assert missing == ["minio"]
    assert duplicates == ["redis"]


def test_container_projection_rejects_wrong_project_label(monkeypatch) -> None:
    module = _module()
    identifier = "a" * 64
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return f'{json.dumps(identifier)}\t"wrong"\t"postgres"\t"running"\tnull\n'

    monkeypatch.setattr(module, "_run_text", fake_run)
    with pytest.raises(module.OpsDoctorError, match="did not match"):
        module._inspect_container_batch([identifier], "context-vault")
    projection = calls[0][calls[0].index("--format") + 1]
    assert "Config.Labels" in projection
    assert "Config.Env" not in projection


def test_compose_files_must_be_regular_and_repo_confined(tmp_path: Path) -> None:
    module = _module()
    outside = tmp_path / "compose.yaml"
    outside.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(module.OpsDoctorError, match="within the repository"):
        module.doctor(
            repo=REPO,
            compose_files=[outside],
            project="context-vault",
            expected_head="cv3_00000006",
            dry_run=True,
        )

    symlink = tmp_path / "compose-link.yaml"
    symlink.symlink_to(COMPOSE_FILE)
    with pytest.raises(module.OpsDoctorError, match="regular"):
        module.doctor(
            repo=tmp_path,
            compose_files=[symlink],
            project="context-vault",
            expected_head="cv3_00000006",
            dry_run=True,
        )


def test_relative_compose_paths_are_repo_root_relative(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = tmp_path / "repo"
    repo.mkdir()
    compose = repo / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = module.doctor(
        repo=repo,
        compose_files=[Path("compose.yaml")],
        project="context-vault",
        expected_head="cv3_00000006",
        dry_run=True,
    )
    assert result["status"] == "DRY_RUN"


def test_compose_file_count_and_size_are_bounded(tmp_path: Path) -> None:
    module = _module()
    compose_files = []
    for index in range(module.MAX_COMPOSE_FILES + 1):
        path = tmp_path / f"compose-{index}.yaml"
        path.write_text("services: {}\n", encoding="utf-8")
        compose_files.append(path)
    with pytest.raises(module.OpsDoctorError, match="count"):
        module.doctor(
            repo=tmp_path,
            compose_files=compose_files,
            project="context-vault",
            expected_head="cv3_00000006",
            dry_run=True,
        )

    oversized = tmp_path / "oversized.yaml"
    oversized.write_bytes(b"x" * (module.MAX_COMPOSE_FILE_BYTES + 1))
    with pytest.raises(module.OpsDoctorError, match="exceeded"):
        module.doctor(
            repo=tmp_path,
            compose_files=[oversized],
            project="context-vault",
            expected_head="cv3_00000006",
            dry_run=True,
        )


def test_long_running_service_contract_requires_ready_unique_containers() -> None:
    module = _module()
    expected = {
        "postgres",
        "redis",
        "minio",
        "backend",
        "worker",
        "migration",
        "minio-bootstrap",
    }
    contract = module._contract_services(expected)
    assert contract == ("postgres", "redis", "minio", "backend", "worker")

    containers = [
        {
            "project": "context-vault",
            "service": "postgres",
            "id": "a" * 64,
            "state": "running",
            "health": "healthy",
        },
        {
            "project": "context-vault",
            "service": "redis",
            "id": "b" * 64,
            "state": "running",
            "health": None,
        },
        {
            "project": "context-vault",
            "service": "minio",
            "id": "c" * 64,
            "state": "running",
            "health": "unhealthy",
        },
        {
            "project": "context-vault",
            "service": "backend",
            "id": "d" * 64,
            "state": "exited",
            "health": None,
        },
        {
            "project": "context-vault",
            "service": "worker",
            "id": "e" * 64,
            "state": "running",
            "health": "healthy",
        },
        {
            "project": "context-vault",
            "service": "migration",
            "id": "f" * 64,
            "state": "exited",
            "health": None,
        },
    ]
    resolved, missing, duplicates = module._resolve_service_containers(
        containers, "context-vault", contract
    )
    assert missing == []
    assert duplicates == []
    assert module._unready_services(resolved) == ["backend", "minio"]


def test_optional_app_services_also_fail_when_missing_or_duplicate() -> None:
    module = _module()
    containers = [
        {
            "project": "context-vault",
            "service": "backend",
            "id": "a" * 64,
            "state": "running",
            "health": "healthy",
        },
        {
            "project": "context-vault",
            "service": "backend",
            "id": "b" * 64,
            "state": "running",
            "health": "healthy",
        },
    ]
    resolved, missing, duplicates = module._resolve_service_containers(
        containers, "context-vault", ("backend", "worker")
    )
    assert resolved == {}
    assert missing == ["worker"]
    assert duplicates == ["backend"]


def test_local_docker_endpoint_fails_closed(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)
    monkeypatch.delenv("DOCKER_CERT_PATH", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker.example:2376")
    with pytest.raises(module.OpsDoctorError, match="local Unix"):
        module._ensure_local_docker()

    monkeypatch.delenv("DOCKER_HOST", raising=False)

    def remote_context(argv, **_kwargs):
        if argv[-1] == "show":
            return "remote\n"
        return '"ssh://operator@example/run/docker.sock"\n'

    monkeypatch.setattr(module, "_run_text", remote_context)
    with pytest.raises(module.OpsDoctorError, match="local Unix"):
        module._ensure_local_docker()


def test_local_unix_docker_endpoint_is_accepted(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)
    monkeypatch.delenv("DOCKER_CERT_PATH", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

    def local_context(argv, **_kwargs):
        if argv[-1] == "show":
            return "desktop-linux\n"
        return '"unix:///Users/test/.docker/run/docker.sock"\n'

    monkeypatch.setattr(module, "_run_text", local_context)
    assert module._ensure_local_docker() == "desktop-linux"


def test_subprocess_output_is_bounded() -> None:
    module = _module()
    with pytest.raises(module.OpsDoctorError, match="exceeded"):
        module._run_bounded([sys.executable, "-c", "print('x' * 1024)"], maximum=64)


def test_subprocess_environment_excludes_application_secrets(monkeypatch) -> None:
    module = _module()
    monkeypatch.setenv("DATABASE_URL", "must-not-propagate")
    monkeypatch.setenv("OBJECT_STORAGE_ENCRYPTION_KEY", "must-not-propagate")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    projected = module._subprocess_env()
    assert projected["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert "DATABASE_URL" not in projected
    assert "OBJECT_STORAGE_ENCRYPTION_KEY" not in projected


def test_doctor_uses_only_secret_free_compose_projections(monkeypatch) -> None:
    module = _module()
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return "a" * 40 + "\n"
        if argv[-3:] == ["--no-interpolate", "--format", "json"]:
            return json.dumps(
                {
                    "services": {
                        "postgres": {},
                        "redis": {},
                        "minio": {},
                        "backend": {},
                    },
                    "volumes": {
                        "postgres_data": {},
                        "redis_data": {},
                        "minio_data": {},
                    },
                }
            )
        raise AssertionError(f"unexpected command: {argv}")

    containers = [
        {
            "project": "context-vault",
            "service": "postgres",
            "id": "a" * 64,
            "state": "running",
            "health": "healthy",
        },
        {
            "project": "context-vault",
            "service": "redis",
            "id": "b" * 64,
            "state": "running",
            "health": "healthy",
        },
        {
            "project": "context-vault",
            "service": "minio",
            "id": "c" * 64,
            "state": "running",
            "health": "healthy",
        },
        {
            "project": "context-vault",
            "service": "backend",
            "id": "d" * 64,
            "state": "running",
            "health": None,
        },
    ]
    monkeypatch.setattr(module, "_run_text", fake_run)
    monkeypatch.setattr(module, "_ensure_local_docker", lambda: "desktop-linux")
    monkeypatch.setattr(module, "_docker_inventory", lambda _project: (containers, []))
    monkeypatch.setattr(module, "_database_head", lambda _url: "cv3_00000006")
    monkeypatch.setattr(module, "_container_probe", lambda _container, _probe: True)

    result = module.doctor(
        repo=REPO,
        compose_files=[COMPOSE_FILE],
        project="context-vault",
        expected_head="cv3_00000006",
        dry_run=False,
        database_url="secret connection value",
    )
    assert result["status"] == "PASS"
    compose_calls = [argv for argv in calls if "compose" in argv]
    assert len(compose_calls) == 1
    assert all("--no-interpolate" in argv for argv in compose_calls)
    assert compose_calls[0][-3:] == ["--no-interpolate", "--format", "json"]
    assert "secret connection value" not in json.dumps(result)
    module._validate_receipt(result)


def test_compose_contract_projection_is_strict_and_bounded() -> None:
    module = _module()
    services, volumes = module._parse_compose_contract(
        json.dumps(
            {
                "services": {"postgres": {}, "backend": {}},
                "volumes": {"postgres_data": {}},
            }
        )
    )
    assert services == {"postgres", "backend"}
    assert volumes == {"postgres_data"}

    for malformed in (
        "not-json",
        "[]",
        '{"services": []}',
        '{"services": {"../escape": {}}}',
        '{"services": {}}',
    ):
        with pytest.raises(module.OpsDoctorError):
            module._parse_compose_contract(malformed)


def test_docker_inventory_uses_full_container_ids(monkeypatch) -> None:
    module = _module()
    identifier = "a" * 64
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-aq"]:
            return identifier + "\n"
        if argv[:3] == ["docker", "volume", "ls"]:
            return ""
        if argv[:2] == ["docker", "inspect"]:
            return (
                json.dumps(identifier)
                + "\t"
                + json.dumps("context-vault")
                + "\t"
                + json.dumps("postgres")
                + "\t"
                + json.dumps("running")
                + "\t"
                + json.dumps("healthy")
                + "\n"
            )
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(module, "_run_text", fake_run)
    containers, volumes = module._docker_inventory("context-vault")
    assert containers[0]["id"] == identifier
    assert volumes == []
    ps_call = next(argv for argv in calls if argv[:3] == ["docker", "ps", "-aq"])
    assert "--no-trunc" in ps_call


def test_caller_supplied_container_identifiers_are_rejected(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "_run_text", lambda *_args, **_kwargs: "a" * 40)
    with pytest.raises(module.OpsDoctorError, match="Docker labels"):
        module.doctor(
            repo=REPO,
            compose_files=[COMPOSE_FILE],
            project="context-vault",
            expected_head="cv3_00000006",
            dry_run=False,
            dependency_containers={"postgres": "attacker-selected"},
        )
