"""Aşama 7: unit tests for ``GitRepositorySource`` safety and metadata.

Uses an injected fake ``git`` runner — no real network clone happens. Verifies:
- commit SHA is captured as both ``commit_sha`` and ``source_revision``;
- a supplied ref produces shallow (``--depth 1 --branch``) clone flags;
- security hardening (``core.autocrlf=false``, ``filter.lfs.required=false``,
  ``core.hooksPath=``) is on the clone argv;
- no hook/script/build/test/package-manager command is ever invoked;
- the real ``GitRunner`` runs with ``shell=False`` and the safe env
  (``GIT_TERMINAL_PROMPT=0``, ``GIT_LFS_SKIP_SMUDGE=1``, ``GIT_CONFIG_NOSYSTEM=1``).
"""

from __future__ import annotations

import os

import pytest

from src.infrastructure.repositories.git_source import GitRepositorySource, GitRunner
from src.infrastructure.repositories.scan_result import ScannedFile


class FakeGitRunner:
    def __init__(self, commit_sha="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"):
        self.calls: list = []
        self.commit_sha = commit_sha

    def run(self, argv, cwd, env=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": env})
        if "rev-parse" in argv and "HEAD" in argv:
            return self.commit_sha
        return ""


def _stub_discovery():
    return lambda root_dir, **kwargs: [
        ScannedFile(relative_path="src/main.py", abs_path="x", content_hash="h")
    ]


def test_scan_captures_metadata_and_flags(tmp_path):
    fake = FakeGitRunner()
    src = GitRepositorySource(
        git_runner=fake, sandbox_factory=lambda: str(tmp_path / "sbx"), discovery=_stub_discovery()
    )
    scan = src.scan("https://github.com/org/repo.git", ref="main")

    assert scan.commit_sha == fake.commit_sha
    assert scan.source_revision == fake.commit_sha
    assert scan.repo_url == "https://github.com/org/repo.git"
    assert scan.branch_or_ref == "main"
    assert len(scan.files) == 1

    clone_calls = [c["argv"] for c in fake.calls if c["argv"][:2] == ["git", "clone"]]
    assert clone_calls, "expected a git clone invocation"
    clone = clone_calls[0]
    assert "--depth" in clone and clone[clone.index("--depth") + 1] == "1"
    assert "--branch" in clone and clone[clone.index("--branch") + 1] == "main"
    # Hardening flags present on the clone command.
    assert "--config" in clone
    assert "core.autocrlf=false" in clone
    assert "filter.lfs.required=false" in clone
    assert next(c for c in clone if c.startswith("core.hooksPath=")).startswith("core.hooksPath=")
    assert "git" in clone
    assert "rev-parse" in [a for c in fake.calls for a in c["argv"]]


def test_no_ref_is_full_clone_without_branch_flag(tmp_path):
    fake = FakeGitRunner()
    src = GitRepositorySource(
        git_runner=fake, sandbox_factory=lambda: str(tmp_path / "sbx"), discovery=_stub_discovery()
    )
    src.scan("https://github.com/org/repo.git")
    clone = [c["argv"] for c in fake.calls if c["argv"][:2] == ["git", "clone"]][0]
    assert "--branch" not in clone
    assert "--depth" not in clone


def test_no_forbidden_command_is_ever_invoked(tmp_path):
    fake = FakeGitRunner()
    src = GitRepositorySource(
        git_runner=fake, sandbox_factory=lambda: str(tmp_path / "sbx"), discovery=_stub_discovery()
    )
    src.scan("https://github.com/org/repo.git", ref="main")

    # Every recorded invocation is the `git` executable itself — we never run
    # a script/build/test/package-manager command (AKTIF_GOREV.md §7.2 "Kod
    # hiçbir şekilde çalıştırılmaz").
    assert fake.calls, "expected at least one git invocation"
    for call in fake.calls:
        assert call["argv"][0] == "git", f"non-git executable invoked: {call['argv']}"
        # Submodules are never pulled automatically.
        assert "submodule" not in call["argv"] and "submodule" not in " ".join(call["argv"]).lower()


def test_real_runner_is_non_shell_and_safe_env(monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, cwd, env, capture_output, text, shell):
        captured["args"] = args
        captured["env"] = env
        captured["shell"] = shell
        captured["cwd"] = cwd
        return _Proc()

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = GitRunner()
    out = runner.run(["git", "rev-parse", "HEAD"], cwd="/tmp")

    assert out == "ok"
    assert captured["shell"] is False
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert captured["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
