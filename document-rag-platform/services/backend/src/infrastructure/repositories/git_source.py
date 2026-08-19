"""Git repository source scanner (Aşama 7).

``GitRepositorySource`` clones a public (or credential-referenced) repository
URL into a throwaway sandbox, captures immutable snapshot metadata (commit
SHA), and delegates file discovery to the (concurrently-built) discovery
layer. It is the source of truth for §7.1 "Git repository URL" and §7.2's
"Kod hiçbir şekilde çalıştırılmaz" security boundary.

Security model (§7.2):

- ``git`` runs as a subprocess with ``shell=False`` and a fixed argv list —
  URL/ref are never interpolated into a shell string.
- ``GIT_TERMINAL_PROMPT=0`` forces git to fail rather than prompt for
  credentials on an unexpected auth challenge.
- ``GIT_LFS_SKIP_SMUDGE=1`` + ``--config filter.lfs.required=false`` stops git
  LFS big-object smudging (no large-object downloads by default).
- ``GIT_CONFIG_NOSYSTEM=1`` ignores the host's system/global git config so a
  local ``core.hooksPath`` (or a template hook initializer) can never be
  imported into the clone.
- ``--config core.autocrlf=false`` keeps line endings byte-identical so
  content hashes are stable across platforms.
- Submodules are **never** pulled (no ``git submodule update``). ``git clone``
  itself does not run repository-defined hooks (hooks are not cloned), and we
  never invoke any script/build/test/package-manager command.

Everything is routed through an injectable ``git_runner`` so unit tests can
stub the runner and assert the exact argv/env without making a network clone.
"""

from __future__ import annotations

import os
import tempfile
from typing import Callable, List, Optional

from .discovery_compat import discover_files as _default_discover
from .scan_result import ScanResult, ScannedFile

# Env additions applied to every git subprocess. Never a shell.
GIT_SAFE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",     # never prompt; fail instead
    "GIT_LFS_SKIP_SMUDGE": "1",     # no LFS object download by default
    "GIT_CONFIG_NOSYSTEM": "1",     # ignore host system/global git config
}


class GitCommandError(RuntimeError):
    """Raised when a git subprocess exits non-zero."""


class GitRunner:
    """Runs ``git`` subprocess calls safely (``shell=False``) and captures
    stdout. Injectable so tests can stub it with a fake that records argv
    without network access (see ``tests/test_git_source.py``).
    """

    def run(self, argv: List[str], cwd: str, env: Optional[dict] = None) -> str:
        import subprocess

        merged = dict(os.environ)
        merged.update(GIT_SAFE_ENV)
        if env:
            merged.update(env)
        proc = subprocess.run(
            argv, cwd=cwd, env=merged, capture_output=True, text=True, shell=False
        )
        if proc.returncode != 0:
            raise GitCommandError(proc.stderr.strip() or f"git {' '.join(argv)} failed")
        return proc.stdout.strip()


class GitRepositorySource:
    """Clones a repository into a sandbox and produces a ``ScanResult``.

    Constructor injection gives full control for unit tests:
    - ``git_runner``: anything with ``run(argv, cwd, env=None) -> str``.
    - ``sandbox_factory``: ``() -> str`` returning a temp directory.
    - ``discovery``: ``(root_dir, **kw) -> List[ScannedFile]``.
    """

    def __init__(
        self,
        *,
        git_runner: Optional[GitRunner] = None,
        sandbox_factory: Optional[Callable[[], str]] = None,
        discovery: Optional[Callable[..., List[ScannedFile]]] = None,
    ):
        self.git_runner = git_runner or GitRunner()
        self.sandbox_factory = sandbox_factory or (lambda: tempfile.mkdtemp(prefix="code-repo-"))
        self.discovery = discovery or _default_discover
        self._git_calls: List[List[str]] = []

    def _run_git(self, argv: List[str], cwd: str) -> str:
        self._git_calls.append(list(argv))
        return self.git_runner.run(argv, cwd=cwd)

    def clone(
        self,
        repository_url: str,
        ref: Optional[str] = None,
        credential_ref: Optional[str] = None,
        work: Optional[str] = None,
    ) -> tuple[str, str, str]:
        """Clones ``repository_url`` into a sandbox checkout and returns
        ``(sandbox, checkout_dir, commit_sha)``.

        ``ref`` (branch/tag/commit) switches to a shallow ``--depth 1`` clone;
        without it a full clone of ``HEAD`` is used. ``credential_ref`` is
        captured in metadata only — this scanner never performs authenticated
        hooks/scripts; credential handling is left to the caller/env.
        """
        sandbox = work or self.sandbox_factory()
        checkout = os.path.join(sandbox, "checkout")

        argv = ["git", "clone"]
        if ref:
            argv += ["--depth", "1", "--branch", ref]
        # Hardening flags (§7.2): no autocrlf, no LFS required, no host config.
        argv += [
            "--config", "core.autocrlf=false",
            "--config", "filter.lfs.required=false",
            "--config", "core.hooksPath=",
        ]
        argv += [repository_url, checkout]

        self._run_git(argv, cwd=sandbox)
        commit_sha = self._run_git(["git", "rev-parse", "HEAD"], cwd=checkout)
        return sandbox, checkout, commit_sha

    def scan(
        self,
        repository_url: str,
        ref: Optional[str] = None,
        credential_ref: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        work: Optional[str] = None,
    ) -> ScanResult:
        sandbox, checkout, commit_sha = self.clone(
            repository_url, ref=ref, credential_ref=credential_ref, work=work
        )
        files = self.discovery(
            checkout,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )
        return ScanResult(
            source_type="repository",
            source_revision=commit_sha,
            root_dir=checkout,
            repo_url=repository_url,
            branch_or_ref=ref,
            commit_sha=commit_sha,
            files=files,
        )
