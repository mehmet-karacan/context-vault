# Release and Rollback

Last verified SHA/date: `fcddb4a49e99c05e7f225fff64c56c0a778fb970`, `2026-09-06` (rollback/DR commands reviewed; no production release).

## Prerequisites
Exact candidate SHA/image digest, CI/security/eval artifacts, backup+restore receipt, migration decision and human release authority.

## Dry-run
Resolve production Compose and run the release verifier; require no open P0/P1 and no secret/source bind/public port.

## Exact commands
Deploy digest-pinned image to canary, run readiness/auth/retrieval/citation smoke, then shift bounded traffic. Do not run migration outside its one-shot gate.

## Expected output
All required receipts bind one SHA/digest; canary is ready and regression/security gates pass.

## Stop conditions
Missing/stale receipt, SHA drift, restore failure, not-ready status, migration ambiguity or open P0/P1.

## Rollback
Shift traffic to the prior digest. If schema is incompatible, restore/cut over to the verified snapshot; never force downgrade.

## Evidence / receipt
Candidate/source/image hashes, approvals, canary metrics, cutover timeline and rollback/close receipt.
