# ADR-012 — Scoped web product and generated API contract

Status: Accepted for local verification; deployment/release gates remain open.
Owner: Mehmet KARACAN
Date: 2026-09-02
Supersedes: handwritten web response types and implicit first-project selection.

The backend response models are the source of the deterministic OpenAPI schema.
The generated TypeScript definitions, form limits and runtime response validator
consume that schema. CI checks both backend/schema and schema/TypeScript drift.
Generated files are never edited by hand.

API-key sessions use explicit workspace admission (`GET /session`). Credentials
exist only in tab memory, never cookies or persistent browser stores. Requests
omit cookies; custom authorization/workspace headers, an explicit CORS allowlist
and HTTPS outside loopback are required. OIDC is not claimed implemented.
Local keyless mode requires an explicit user action and backend confirmation.
Deployment config is rendered per request, not compiled into client chunks.

Changing project remounts the scoped document/upload/conversation features and
aborts old polling. Conversation IDs are carried only inside the same scope.
Upload retries retain the same idempotency key; after acceptance they resume
polling the same job. Terminal failures remain visible. Sub-stage percentages
are unknown until measured; completed jobs alone can show 100 percent.

The evidence rail lists only used citations, immutable version/locator data and
escaped excerpts. No legacy candidate/source fallback, raw HTML, remote Markdown
images or confidence percentages. No-answer/provider/policy/permission states
remain distinct. Admin diagnostics are unavailable in production and role-gated
in development, and expose only allowlisted ranks, aggregate timing and fallback.

HTTP chat owns the answer transaction: the application service flushes, the
endpoint commits before success. The connected browser test exposed the missing
commit; the fix is checked from a separate connection after source soft deletion.

Evidence: `tests/test_web_contracts.py`, `tests/test_openapi_export.py`, web unit
and Playwright suites, `tests/verify_browser_fixture.py`, A10 product receipt.
Local deterministic models are integration fixtures, not real-provider quality.
