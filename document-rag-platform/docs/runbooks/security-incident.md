# Security Incident and Public Secret Exposure

Last verified SHA/date: `b6d368c4ba75afee5630251e5c0f3649d1c9c920`, `2026-09-06` (executable redaction/log-containment drills; no live revocation).

## Prerequisites
Incident commander, secret owners, audit retention and legal/notification policy.

## Dry-run
Hash the suspected secret and search history/artifacts/log policy for the hash or
secret-shaped fields without copying the value. Exercise only synthetic fixtures:

```sh
cd document-rag-platform/services/backend
.venv/bin/python -m pytest -q \
  tests/test_redaction.py \
  tests/test_observability.py::test_structured_formatter_drops_sensitive_and_unknown_fields \
  tests/test_observability.py::test_generic_error_hides_stack_trace_when_not_debug
```

This command verifies redaction and containment; it performs no revocation,
rotation, session invalidation or external notification.

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
