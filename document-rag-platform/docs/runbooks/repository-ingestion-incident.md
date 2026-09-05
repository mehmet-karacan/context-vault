# Repository Ingestion Incident and SSRF

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (security controls/runbook review; no hostile network call).

## Prerequisites
Incident ID, egress-control authority, repository feature flag and audit-log access.

## Dry-run
Disable new repository admissions and inspect hashed host/URL decisions, DNS answers and active jobs without cloning.

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
