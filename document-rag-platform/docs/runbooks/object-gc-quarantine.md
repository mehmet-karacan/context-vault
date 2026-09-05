# Object GC and Quarantine

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (reconciliation/receipt path exercised on disposable storage).

## Prerequisites
Fresh inventory, DB snapshot, retention/legal-hold policy and owner-approved grace period.

## Dry-run
Run reconciliation and GC with `dry_run=true`; review only hashed object keys and counts.

## Exact commands
Quarantine selected eligible objects through the application maintenance service; delete only after a second inventory and grace expiry.

## Expected output
Missing count stays zero; every action has `planned|skipped|deleted|failed` receipt.

## Stop conditions
Legal hold, referenced object, inventory drift, missing DB object or unavailable backup.

## Rollback
Restore quarantined bytes from the bound backup/version; never empty a bucket or delete a volume.

## Evidence / receipt
Inventory/reconciliation hashes, policy/grace time, action counts and GC receipt hashes.
