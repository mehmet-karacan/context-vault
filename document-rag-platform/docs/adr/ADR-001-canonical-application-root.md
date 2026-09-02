# ADR-001 — Canonical Application Root

- **Status:** Accepted
- **Date:** 2026-08-20
- **Owner:** Mehmet KARACAN
- **Supersedes:** none
- **Evidence:** root `README.md`; `artifacts/repository/2026-09-02-tree-inventory.json`

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
- Repo-root application skeleton dirs (`apps/`, `services/`, `tests/`,
  `packages`, `infra` and `docs/adr`) are **not** canonical and are removed.
- Root historical coordination views (`active`, `done`, `plan` and the remaining
  root documents) are not application source and are explicitly classified.
- Nothing new is authored under the root skeleton dirs.

## Consequences

- One `docker-compose.yml` / `Dockerfile` / `package.json` source per concern;
  no duplicated skeleton to keep in sync.
- Empty skeleton cleanup is recorded in the tree inventory; directories carrying
  real content require an explicit classification decision before removal.
- All ADR paths and documentation assume `document-rag-platform/` as the root.
