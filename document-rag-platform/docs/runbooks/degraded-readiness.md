# Degraded Readiness and Provider Outage

Last verified SHA/date: `7d36fc74143d7504113ba6ac3adeaf9d736537bc`, `2026-09-06` (real disposable dependency doctor probes PASS).

## Prerequisites
Health endpoint access, dependency ownership and capability criticality map.

## Dry-run
Run `scripts/ops_doctor.py` and query `/health/live` plus `/health/readiness`; retain redacted results.

## Exact commands
Remove an unhealthy instance from traffic, pause affected capability/jobs and restore the dependency; do not mark global ready for a mandatory dependency failure.

## Expected output
Mandatory failure returns `503`; optional provider reports capability-specific degraded status without secrets.

## Stop conditions
False-ready response, unknown migration head, queue split-brain or raw endpoint/credential disclosure.

## Rollback
Route to the last healthy deployment and keep queued durable work unacknowledged until dependency recovery.

## Evidence / receipt
UTC timeline, dependency/status hashes, HTTP status, queue age and recovery receipt.
