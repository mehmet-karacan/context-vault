# Runbook — Web scope, contract and product gates

Owner: Mehmet KARACAN. Last verified date: 2026-09-02.
Last verified SHA: see `artifacts/product/2026-09-02-a10/PRODUCT_RECEIPT.json`.
Scope: local synthetic verification; remote CI and release are not asserted.

## Prerequisites and admission

Use the canonical root and task branch. Backend schema must be at the recorded
V3 head. Never point the browser fixture at a user database: it explicitly accepts
only database `cv3_a10_browser` plus `CV3_BROWSER_TEST_MODE=1`. The fixture seeds a
separate active identity; migration's inactive legacy principal remains untouched.
MinIO fixtures contain synthetic files only. Real providers/private data are not
used. The fixture runs real parsing, worker core, PostgreSQL retrieval and encrypted
MinIO; queue dispatch is synchronous, not a Celery delivery quality claim.

## Normal deployment configuration

Set `CONTEXT_VAULT_API_BASE_URL` when starting the web server (same-origin proxy if
empty). Set `CONTEXT_VAULT_AUTH_MODE=disabled` only for the explicit local keyless
button; backend still enforces loopback-only local mode. Default web mode is
`api_key`. Never put API keys into these public runtime variables. Authenticate
with a valid workspace/key; neither a page URL nor the first project grants scope.
Page reload signs out. Navigation/project change closes the current chat view;
background backend work continues and can be inspected on `/jobs`.

## Dry-run and exact local gates

From repository root, with the locked backend environment already installed:

```sh
document-rag-platform/services/backend/.venv/bin/python scripts/generate_openapi.py --check
```

From `document-rag-platform/apps/web`:

```sh
npm ci --ignore-scripts
npm run api-client-check
npm run lint
npm run typecheck
npm run unit
npm run build
npm run bundle-check
npm run e2e-smoke
```

Expected: generated schema/client unchanged, all tests green, WCAG scan empty.
The bundle scan is a bounded credential/config-pattern scan, not a guarantee of
absence of all possible secrets. Node tooling may emit harmless color-environment
warnings; no test failure is waived.

For the opt-in connected suite, first create the explicitly named **new, blank**
fixture database with the usual DB admin tool, migrate it, and set DATABASE_URL,
MinIO/Redis settings and the test encryption key from your isolated environment.
No real credentials belong in commands committed to this repository.

From backend, in a foreground terminal:

```sh
CV3_BROWSER_TEST_MODE=1 .venv/bin/python tests/browser_fixture_server.py
```

From web, using the production build:

```sh
CV3_BROWSER_LIVE=1 PLAYWRIGHT_JUNIT_OUTPUT_FILE=reports/live-e2e-junit.xml npx playwright test live.spec.ts
```

Then from backend with the same isolated environment:

```sh
.venv/bin/python tests/verify_browser_fixture.py
```

Expected fresh-connection result: two messages, one claim, one encrypted citation,
one soft-deleted document, at least three retained encrypted objects. This check is
mandatory: a citation visible in the browser alone does not prove persistence.
The CI RAG workflow encodes the whole connected setup with public test-only values.

## Stop, recovery, rollback and evidence

Stop if scope, schema, auth, persistence or object retention differs from expected.
Do not delete/reset the database, regenerate vectors, retry under a new upload key
or fall back to an unscoped query. Reconnect/retry the existing job ID/key. A failed
terminal job needs operator diagnosis; the UI does not invent a retry endpoint.
Stop the foreground fixture server with Ctrl-C; preserve its DB/object evidence.
No live schema migration is introduced by this frontend change. An application
rollback must retain the fixed chat commit boundary and existing V3 schema.

Record exact implementation SHA, all JUnit files, screenshots and the independent
connection result in the product receipt. Do not put real keys or document content
in public artifacts. Quarantined/permission errors expose safe UI labels only.
