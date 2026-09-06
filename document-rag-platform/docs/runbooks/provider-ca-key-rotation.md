# Provider, CA and Key Rotation

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (environment/Compose contract review only; no live rotation).

## Prerequisites
Two-person secret authority, overlap window, backup/restore receipt and
documented credential consumers. In the production Compose contract, backend
and worker are the provider-capable consumers. The trust bundle is an external
secret named by `PROVIDER_CA_SECRET_NAME` and is mounted read-only at
`/run/secrets/provider_ca_bundle`; it is not an image build input.

## Dry-run
Validate the new CA chain and credential in a disposable deployment; never
print values. Resolve the production contract with:

`docker compose --env-file .env -f compose.yaml -f compose.prod.example.yaml config --quiet`

Then verify the mounted file is a regular, non-symlink, non-empty PEM trust
bundle. Do not use `curl -k`, `verify=False` or another TLS bypass as a test.

## Exact commands
Create a new version of the external secret in the deployment platform, keep
the old CA in the overlap bundle, and update the secret reference atomically.
Restart one worker/backend canary, verify real provider TLS and capability, then
roll the remaining provider consumers. Revoke the old provider credential and
remove the old CA only after every consumer is on the new bundle.

## Expected output
The entrypoint CA guard passes before the application process starts; TLS
verification, readiness and capability smoke pass; no secret appears in image,
logs or receipts. A missing, mismatched, symlinked, empty or invalid bundle must
stop the provider-capable service before network use.

## Stop conditions
Single-key destructive rotation, unavailable old key, TLS bypass, decrypt failure or secret projection.

## Rollback
Restore the prior external-secret version during the overlap window and return
traffic to the prior deployment; never rebuild the image merely to roll back a
CA and never discard required object-encryption keys.

## Evidence / receipt
Only credential identifier hashes, CA fingerprint, canary results, revocation time and approver identity.
