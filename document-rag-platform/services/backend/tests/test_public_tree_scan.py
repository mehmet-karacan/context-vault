from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts/check_public_tree.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_public_tree", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scanner = _load_module()


def _repository(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def test_scan_covers_tracked_files_outside_legacy_source_roots(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {
            "README.md": "safe\n",
            "artifacts/release/summary.json": (
                "private." + "turktelekom" + ".com.tr\n"
            ),
        },
    )

    receipt = scanner.scan_repository(repo)

    assert receipt["status"] == "FAIL"
    assert receipt["scope"] == ["all-git-tracked-files"]
    assert receipt["tracked_files_checked"] == 2
    assert receipt["findings"] == [
        {"rule": "private-endpoint", "path": "artifacts/release/summary.json"}
    ]


@pytest.mark.parametrize(
    "suffix",
    [".dump", ".log", ".pem", ".crt", ".tar", ".zip", ".tgz", ".xz", ".zst"],
)
def test_scan_rejects_runtime_and_key_artifact_suffixes(
    tmp_path: Path, suffix: str
) -> None:
    repo = _repository(tmp_path, {f"artifacts/runtime{suffix}": "redacted\n"})

    receipt = scanner.scan_repository(repo)

    assert receipt["status"] == "FAIL"
    assert receipt["findings"] == [
        {
            "rule": "private-or-runtime-artifact",
            "path": f"artifacts/runtime{suffix}",
        }
    ]


def test_scan_checks_symlink_value_without_following_target(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {"README.md": "safe\n"})
    outside = tmp_path / ("private." + "turktelekom" + ".com.tr")
    outside.write_text("must not be read\n", encoding="utf-8")
    link = repo / "artifact-link"
    link.symlink_to(outside)
    subprocess.run(["git", "-C", str(repo), "add", "artifact-link"], check=True)

    receipt = scanner.scan_repository(repo)

    assert receipt["status"] == "FAIL"
    assert receipt["findings"] == [
        {"rule": "private-endpoint", "path": "artifact-link"}
    ]


def test_write_receipt_is_private_and_refuses_symlink(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("old\n", encoding="utf-8")
    receipt_path.chmod(0o644)
    scanner.write_receipt(receipt_path, {"status": "PASS"})

    assert json.loads(receipt_path.read_text(encoding="utf-8")) == {"status": "PASS"}
    assert receipt_path.stat().st_mode & 0o777 == 0o600

    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("platform does not provide O_NOFOLLOW")
    target = tmp_path / "target.json"
    target.write_text("unchanged\n", encoding="utf-8")
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(target)

    with pytest.raises(RuntimeError, match="unsafe receipt path"):
        scanner.write_receipt(symlink, {"status": "FAIL"})
    assert target.read_text(encoding="utf-8") == "unchanged\n"


def test_scan_does_not_skip_ascii_signature_in_malformed_utf8(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {"artifact.txt": "placeholder\n"})
    artifact = repo / "artifact.txt"
    artifact.write_bytes(b"\xffprivate." + b"turktelekom" + b".com.tr\n")

    receipt = scanner.scan_repository(repo)

    assert receipt["status"] == "FAIL"
    assert receipt["findings"] == [{"rule": "private-endpoint", "path": "artifact.txt"}]


def test_deleted_index_entry_is_not_counted_as_checked(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {"present.txt": "safe\n", "deleted.txt": "gone\n"})
    (repo / "deleted.txt").unlink()

    receipt = scanner.scan_repository(repo)

    assert receipt["tracked_files_total"] == 2
    assert receipt["tracked_files_checked"] == 1
