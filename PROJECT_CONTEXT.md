# Context Vault Project Context

## Purpose and boundaries

Context Vault is a local-first document and repository retrieval system with a
PostgreSQL-backed continuity layer. It ingests approved project sources,
retrieves scoped evidence, produces citation-bound answers, and coordinates
work through explicit claims, receipts, approvals, and immutable context
manifests.

This repository does not authorize an agent, model, CLI, Markdown file, Git
history, or vector index to become the operational source of truth. PostgreSQL
Work Graph records are authoritative for active work and effects. Approved,
versioned knowledge records are authoritative for durable decisions.

## Canonical paths

- `AKTIF_GOREV.md`: current implementation task, gates, and evidence log.
- `.contextvault/project.yaml`: machine-readable project identity and policy.
- `document-rag-platform/services/backend/`: FastAPI backend, workers, Alembic,
  domain code, and backend tests.
- `document-rag-platform/apps/web/`: frontend application and tests.
- `document-rag-platform/docs/runbooks/`: operational procedures.
- `document-rag-platform/artifacts/`: public-safe evidence only.

## Setup, validation, and execution

Run backend commands from `document-rag-platform/services/backend/`:

```text
uv sync --frozen
uv run pytest -q -m "not integration"
uv run ruff check src tests
uv run alembic upgrade head
```

Validate deployment configuration from `document-rag-platform/` with the
documented Compose environment and runbooks. Production or user-data migration,
restore, credential rotation, release, and remote mutation must follow the
applicable claim, approval, backup, and receipt gates.

## Critical rules

- Preserve PostgreSQL data, object artifacts, embedding indexes, and user files.
- Never replace migration recovery with an unproven reset.
- Claim and fencing-token admission must precede every external effect.
- Expected revisions are fail-closed; drift requires a new prepare or recovery.
- Completion requires a terminal receipt and acceptance evidence.
- Raw prompts, document content, secrets, PII, and private evaluation data do
  not belong in logs, public artifacts, or Git.
- Retrieval and vector projections are reconstructable and are not authority.
- Models and CLIs cannot approve their own work or promote their own output to
  approved knowledge.

## Data classification and provider policy

The supported classifications are `public`, `internal`, `confidential`, and
`restricted`. Remote-provider routing is allowed only when the machine-readable
manifest and exact provider/model record both admit the classification.
`restricted` and explicitly local-only context must never be auto-loaded into a
remote-provider request.

## Known risks and runbooks

Current risks and incomplete acceptance gates are recorded in
`AKTIF_GOREV.md`. Incident, backup/restore, migration recovery, degraded
readiness, key/CA rotation, object GC, repository ingestion, and release rollback
procedures live under `document-rag-platform/docs/runbooks/`.

## Canonical and non-canonical content

Human-authored sections in this file describe stable project intent and
constraints. Machine-generated context, status, receipts, and handoffs are not
written into this file; they are projections of validated PostgreSQL records and
immutable artifacts. Any future generated section must be delimited, validated,
and replaced only by the Context Vault projection command. Editing a projection
does not mutate canonical state.
