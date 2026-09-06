"""Regression tests for owner authors and narrow server-side committers."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts/check_commit_ownership.py"
OWNER_NAME = "Mehmet KARACAN"
OWNER_EMAIL = "karacan.mehmet@hotmail.com"
GITHUB_NAME = "GitHub"
GITHUB_EMAIL = "noreply@github.com"


def _module():
    spec = importlib.util.spec_from_file_location("check_commit_ownership", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _commit(
    repo: Path,
    *,
    author_name: str = OWNER_NAME,
    author_email: str = OWNER_EMAIL,
    committer_name: str = GITHUB_NAME,
    committer_email: str = GITHUB_EMAIL,
    message: str = "server-side squash merge",
) -> None:
    marker = repo / "marker.txt"
    marker.write_text(message, encoding="utf-8")
    _git(repo, "add", "marker.txt")
    environment = {
        "PATH": __import__("os").environ["PATH"],
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": committer_name,
        "GIT_COMMITTER_EMAIL": committer_email,
    }
    _git(repo, "commit", "-m", message, env=environment)


def _repo(tmp_path: Path, **commit_kwargs: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet")
    _commit(repo, **commit_kwargs)
    return repo


def test_github_committer_is_rejected_without_exact_exception(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path)
    assert module.main(["--repo", str(repo)]) == 1


def test_exact_github_committer_exception_keeps_owner_author_required(
    tmp_path: Path,
) -> None:
    module = _module()
    repo = _repo(tmp_path)
    assert (
        module.main(
            [
                "--repo",
                str(repo),
                "--allowed-committer",
                f"{GITHUB_NAME} {GITHUB_EMAIL}",
            ]
        )
        == 0
    )

    unowned = _repo(
        tmp_path / "unowned",
        author_name=GITHUB_NAME,
        author_email=GITHUB_EMAIL,
    )
    assert (
        module.main(
            [
                "--repo",
                str(unowned),
                "--allowed-committer",
                f"{GITHUB_NAME} {GITHUB_EMAIL}",
            ]
        )
        == 1
    )


def test_committer_exception_never_allows_coauthor_trailer(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(
        tmp_path,
        message=(
            "server-side squash merge\n\n"
            "Co-Authored-By: Another Person <another@example.com>"
        ),
    )
    assert (
        module.main(
            [
                "--repo",
                str(repo),
                "--allowed-committer",
                f"{GITHUB_NAME} {GITHUB_EMAIL}",
            ]
        )
        == 1
    )


def test_invalid_committer_exception_is_usage_error(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path)
    assert (
        module.main(
            ["--repo", str(repo), "--allowed-committer", "missing-email-separator"]
        )
        == 2
    )
