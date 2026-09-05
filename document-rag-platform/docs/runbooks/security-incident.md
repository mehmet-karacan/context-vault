# Security Incident and Public Secret Exposure

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (procedure review; no live revocation).

## Prerequisites
Incident commander, secret owners, audit retention and legal/notification policy.

## Dry-run
Hash the suspected secret and search history/artifacts/log policy for the hash or secret-shaped fields without copying the value.

## Exact commands
Contain egress, revoke/rotate the exposed credential at its authority, invalidate sessions/API keys, and preserve immutable audit evidence.

## Expected output
Old credential is rejected, new credential passes canary, public artifact contains no raw secret.

## Stop conditions
Unverified blast radius, incomplete revocation, deletion of audit evidence or plaintext secret in tickets/chat.

## Rollback
Credentials are not un-revoked; roll application traffic back while keeping replacement credentials and containment.

## Evidence / receipt
Incident/timeline, secret hash only, affected-system hashes, revocation and canary receipts.
