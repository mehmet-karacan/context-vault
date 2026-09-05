"""Repository-level regression checks for the A2 supply-chain contract."""

from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
COMPOSE = REPO / "document-rag-platform/docker-compose.yml"
WORKFLOWS = REPO / ".github/workflows"


def _compose_service_image(service: str) -> str:
    lines = COMPOSE.read_text(encoding="utf-8").splitlines()
    marker = f"  {service}:"
    try:
        start = lines.index(marker) + 1
    except ValueError as exc:
        raise AssertionError(f"missing compose service: {service}") from exc

    for line in lines[start:]:
        if line.startswith("  ") and not line.startswith("    "):
            break
        if line.startswith("    image: "):
            return line.removeprefix("    image: ").strip()
    raise AssertionError(f"missing image for compose service: {service}")


def test_stateful_compose_images_are_digest_pinned() -> None:
    for service in ("postgres", "redis", "minio"):
        image = _compose_service_image(service)
        assert re.fullmatch(
            r"[^\s@]+(?:\:[^\s@]+)?@sha256:[0-9a-f]{64}", image
        ), f"{service} image is not pinned to an immutable sha256 digest: {image}"


def test_third_party_actions_are_commit_pinned() -> None:
    action_ref = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for reference in action_ref.findall(workflow.read_text(encoding="utf-8")):
            if reference.startswith("./"):
                continue
            _, separator, revision = reference.rpartition("@")
            assert separator and re.fullmatch(
                r"[0-9a-f]{40}", revision
            ), f"{workflow.name} has a mutable action reference: {reference}"


def test_security_image_build_cannot_reuse_stale_package_layers() -> None:
    security = (WORKFLOWS / "ci-security.yml").read_text(encoding="utf-8")
    assert "docker build --pull --no-cache" in security
