# ADR-001 — Canonical Application Root

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Context Vault platform implementation (Aşama 10)

## Context

The repository root contains a set of top-level skeleton directories
(`apps/`, `services/`, `docs/`, `tests/`, `packages/`, `infra/`) that mirror the
names of directories under `document-rag-platform/`. Only the sub-tree under
`document-rag-platform/` holds real code and configuration; the root-level
entries are empty `.gitkeep` skeletons and are NOT used by any build.

A single source of truth is required so builds, config, documentation and
future operations never diverge (see `AKTIF_GOREV.md` Aşama 10 & `README`).

## Decision

- `document-rag-platform/` is the single canonical application root for the
  Context Vault / RAG platform.
  - Backend: `document-rag-platform/services/backend/src/`
  - Frontend: `document-rag-platform/apps/web/`
  - Compose: `document-rag-platform/docker-compose.yml`
- Repo-root skeleton dirs (`apps/`, `services/`, `docs/`, `tests/`,
  `packages/`, `infra/`) are **not** canonical and are listed (not deleted) in
  `docs/cleanup-candidates.md` (see section "Yinelenen / iskelet dizinler").
- Nothing new is authored under the root skeleton dirs.

## Consequences

- One `docker-compose.yml` / `Dockerfile` / `package.json` source per concern;
  no duplicated skeleton to keep in sync.
- Cleanup remains a checklist that requires explicit approval
  (`AKTIF_GOREV.md` Bölüm 4 madde 12 — nothing is auto-deleted).
- All ADR paths and documentation assume `document-rag-platform/` as the root.
