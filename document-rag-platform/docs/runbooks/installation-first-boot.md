# Installation and First Boot

Last verified SHA/date: `7d36fc74143d7504113ba6ac3adeaf9d736537bc`, `2026-09-06` (disposable base/dev/CI Compose startup, blank migration and real doctor PASS).

## Prerequisites
Clean clone, Docker Compose v2, real non-default secrets, approved image digest,
and empty named volumes. Define distinct PostgreSQL migration-owner and runtime
roles and credentials through `POSTGRES_ADMIN_*`, `POSTGRES_RUNTIME_*`,
`DATABASE_MIGRATION_URL` and `DATABASE_RUNTIME_URL`. The two URLs must never
identify the same role.

The production overlay also requires an externally managed PEM trust bundle
named by `PROVIDER_CA_SECRET_NAME`. Provision it in the deployment platform as
a secret; do not copy it into this repository, an image layer, `.env`, logs or
an evidence artifact.

`10-runtime-role.sql` is an empty-cluster initializer. If the PostgreSQL volume
already contains a cluster, stop: the image will not rerun the initializer.
Create or reconcile the restricted runtime role through an approved,
credential-private database administration procedure, then run the migration
one-shot. Never delete an existing volume to make role initialization run.

## Dry-run
`docker compose --env-file .env -f compose.yaml -f compose.prod.example.yaml config --quiet`

## Exact commands
`docker compose --env-file .env -f compose.yaml -f compose.prod.example.yaml up -d postgres redis minio minio-bootstrap migration backend worker scheduler`

## Expected output
Migration and bootstrap exit `0`; the migration log includes only
`runtime database grants: PASS`; backend readiness is healthy. Backend, worker
and scheduler use only `DATABASE_RUNTIME_URL`; only the one-shot migration gets
`DATABASE_MIGRATION_URL`. Backend and worker refuse to start if the provider CA
secret is missing, inconsistent or not a valid trust bundle.

Run the doctor with the backend lock environment
(`uv run python ../../../scripts/ops_doctor.py ...` from `services/backend`) and
require `PASS`.

## Stop conditions
Placeholder secret/digest, shared migration/runtime identity, an already
initialized cluster without the restricted role, missing/invalid provider CA,
public port, source bind, migration/grant failure, unhealthy dependency, or
doctor finding.

## Rollback
`docker compose ... down` without `--volumes`; keep database and object volumes.

## Evidence / receipt
Store resolved-config hash, image digests, health output and doctor receipt.
Record only role names and grant-verification outcome, never URLs or credential
values. Never store resolved secret values or CA content.
