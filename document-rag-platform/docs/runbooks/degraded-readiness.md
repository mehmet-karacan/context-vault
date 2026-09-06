# Degraded Readiness and Provider Outage

Last verified SHA/date: `7d36fc74143d7504113ba6ac3adeaf9d736537bc`, `2026-09-06` (real disposable dependency doctor probes PASS).

## Prerequisites
Health endpoint access, dependency ownership and capability criticality map.

## Dry-run
Run `scripts/ops_doctor.py` and query `/health/live` plus `/health/readiness`;
retain redacted results. Exercise fail-closed mandatory and optional-provider
classification without a live outage:

```sh
cd document-rag-platform/services/backend
.venv/bin/python -m pytest -q \
  tests/test_health.py::test_readiness_is_degraded_not_crash_when_dependency_down \
  tests/test_health.py::test_optional_provider_returns_capability_degraded_without_details \
  tests/test_observability.py::test_readiness_treats_raising_checker_as_down
```

All dependencies are fakes; this command does not stop a service or call a provider.

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
