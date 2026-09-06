# Repository Ingestion Incident and SSRF

Last verified SHA/date: `b6d368c4ba75afee5630251e5c0f3649d1c9c920`, `2026-09-06` (executable SSRF admission drills; no hostile network call).

## Prerequisites
Incident ID, egress-control authority, repository feature flag and audit-log access.

## Dry-run
Disable new repository admissions and inspect hashed host/URL decisions, DNS
answers and active jobs without cloning. The bounded local drill is:

```sh
cd document-rag-platform/services/backend
.venv/bin/python -m pytest -q \
  tests/test_git_source.py::test_repository_url_rejects_unsafe_protocol_or_credentials \
  tests/test_git_source.py::test_repository_url_rejects_non_public_dns_results \
  tests/test_git_source.py::test_repository_url_rejects_host_outside_allowlist \
  tests/test_git_source.py::test_real_runner_is_non_shell_and_safe_env
```

DNS is mocked and the runner is intercepted; no clone or hostile network request
is performed.

## Exact commands
Set `FEATURE_REPOSITORY_INGESTION=false` in deployment configuration, restart app workers, cancel affected jobs through the scoped API, then rotate exposed credentials.

## Expected output
Repository endpoint rejects requests; no private/link-local address is contacted; affected jobs terminate with receipts.

## Stop conditions
Unknown scope, uncontained egress, raw URL/credential logging or missing audit trail.

## Rollback
Keep feature disabled; restore only after allow-list/DNS-rebinding regression and security approval.

## Evidence / receipt
Hashed hosts/jobs, egress rule revision, audit events and credential-revocation receipt.
