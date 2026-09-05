# Installation and First Boot

Last verified SHA/date: `7d36fc74143d7504113ba6ac3adeaf9d736537bc`, `2026-09-06` (disposable base/dev/CI Compose startup, blank migration and real doctor PASS).

## Prerequisites
Clean clone, Docker Compose v2, real non-default secrets, approved image digest, and empty named volumes.

## Dry-run
`docker compose --env-file .env -f compose.yaml -f compose.prod.example.yaml config --quiet`

## Exact commands
`docker compose --env-file .env -f compose.yaml -f compose.prod.example.yaml up -d postgres redis minio minio-bootstrap migration backend worker scheduler`

## Expected output
Migration and bootstrap exit `0`; backend readiness is healthy; run the doctor
with the backend lock environment (`uv run python ../../../scripts/ops_doctor.py ...`
from `services/backend`) and require `PASS`.

## Stop conditions
Placeholder secret/digest, public port, source bind, migration failure, unhealthy dependency, or doctor finding.

## Rollback
`docker compose ... down` without `--volumes`; keep database and object volumes.

## Evidence / receipt
Store resolved-config hash, image digests, health output and doctor receipt. Never store resolved secret values.
