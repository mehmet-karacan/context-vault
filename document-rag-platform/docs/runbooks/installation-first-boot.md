# Installation and First Boot

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (Compose resolution and doctor syntax).

## Prerequisites
Clean clone, Docker Compose v2, real non-default secrets, approved image digest, and empty named volumes.

## Dry-run
`docker compose --env-file .env -f compose.yaml -f compose.prod.example.yaml config --quiet`

## Exact commands
`docker compose --env-file .env -f compose.yaml -f compose.prod.example.yaml up -d postgres redis minio minio-bootstrap migration backend worker scheduler`

## Expected output
Migration and bootstrap exit `0`; backend readiness is healthy; `python scripts/ops_doctor.py ...` returns `PASS`.

## Stop conditions
Placeholder secret/digest, public port, source bind, migration failure, unhealthy dependency, or doctor finding.

## Rollback
`docker compose ... down` without `--volumes`; keep database and object volumes.

## Evidence / receipt
Store resolved-config hash, image digests, health output and doctor receipt. Never store resolved secret values.
