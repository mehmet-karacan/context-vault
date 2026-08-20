# ADR-005 — Repository Scan Security Model

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Context Vault platform implementation (Aşama 7)

## Context

Ingesting git repositories, archives and local directories runs untrusted
third-party code through parsing, embedding and indexing. The pipeline must
never execute repository content or scrape secrets, and a web-requested path
must never escape its allowed root (`AKTIF_GOREV.md §7.2`).

## Decision

- **Canonical path enforcement** — `infrastructure/repositories/path_security.py`:
  `canonical` (expanduser + abspath + realpath = symlink-expanded), and
  `is_allowed_scan_path` / `canonical_under_root` allow only paths that
  canonicalize under one of `CODE_ALLOWED_ROOTS` (`config.py` default
  `"/imports,/workspace"`). Relative and symlink-escape paths are refused.
  Used by `api/v1/repositories.py` and `repositories/directory_source.py`.
- **Ignore precedence** — `infrastructure/repositories/ignore_rules.py`, in
  order: 1) system security ignore list (never overridable), 2)
  `.contextvaultignore`, 3) repository `.gitignore`, 4) user include/exclude
  patterns. A built-in gitignore-style glob matcher never shells out to git.
  `is_sensitive_path` skips secrets/credentials by default
  (`CODE_SECRET_POLICY = "skip"`).
- **Git never executes** — `infrastructure/repositories/git_source.py`
  (`GitRepositorySource`): `git` runs as `shell=False` subprocess with fixed
  argv; `GIT_TERMINAL_PROMPT=0`, `GIT_LFS_SKIP_SMUDGE=1`,
  `GIT_CONFIG_NOSYSTEM=1`, `core.autocrlf=false`; submodules are never pulled
  (`CODE_ALLOW_SUBMODULES = False`); no hooks/scripts/builds run.
- **Archive guards** — `infrastructure/repositories/archive_source.py`
  (`ArchiveSourceScanner`): path-traversal protection per member, plus
  zip-bomb limits (`CODE_ARCHIVE_MAX_ENTRIES`, `CODE_ARCHIVE_MAX_ENTRY_BYTES`,
  `CODE_ARCHIVE_MAX_TOTAL_BYTES`), raising `ArchiveLimitError` to refuse the
  whole archive when limits are exceeded.
- Limits/symlinks/secrets are configurable (`CODE_MAX_FILES`,
  `CODE_MAX_TOTAL_BYTES`, `CODE_MAX_FILE_BYTES`, `CODE_SCAN_TIMEOUT_SECONDS`,
  `CODE_FOLLOW_SYMLINKS=False`).
- Whole feature is gated behind `FEATURE_REPOSITORY_INGESTION`
  (`config.py`, default `True`); `api/v1/repositories.py` refuses to run when
  it is off.

## Consequences

- Remote code is never executed and secrets are never scraped; scans stay
  inside `CODE_ALLOWED_ROOTS`.
- Security boundaries are centralized and configurable, giving operations a
  single place to tighten limits.
- Borrowed content-hash stability enables reliable incremental re-index
  (`reindex_service.py`) without resorting to shelling out.
