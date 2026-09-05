# Provider, CA and Key Rotation

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (environment/Compose contract review only; no live rotation).

## Prerequisites
Two-person secret authority, overlap window, backup/restore receipt and documented credential consumers.

## Dry-run
Validate the new CA chain and credential in a disposable deployment; never print values: `docker compose ... config --quiet`.

## Exact commands
Install CA via deployment secret/mount, add the new provider/storage credential, restart one worker/backend canary, verify, then revoke the old credential.

## Expected output
TLS verification, readiness and capability smoke pass; no secret appears in image, logs or receipts.

## Stop conditions
Single-key destructive rotation, unavailable old key, TLS bypass, decrypt failure or secret projection.

## Rollback
Re-enable old credential/CA during overlap and return traffic to the prior deployment; never discard required object-encryption keys.

## Evidence / receipt
Only credential identifier hashes, CA fingerprint, canary results, revocation time and approver identity.
