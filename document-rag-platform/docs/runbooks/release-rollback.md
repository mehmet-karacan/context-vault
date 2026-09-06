# Release and Rollback

Last verified SHA/date: `b6d368c4ba75afee5630251e5c0f3649d1c9c920`, `2026-09-06` (executable fail-closed candidate drills; no production release or rollback).

## Prerequisites
Exact candidate SHA/image digest, CI/security/eval artifacts, backup+restore receipt, migration decision and human release authority.

## Dry-run
Resolve production Compose and run the release verifier; require no open P0/P1
and no secret/source bind/public port. The no-deployment local drill is:

```sh
cd document-rag-platform/services/backend
.venv/bin/python -m pytest -q \
  tests/test_a11_release_gate.py::test_rc_open_p1_and_dirty_worktree_fail_closed \
  tests/test_a11_release_gate.py::test_rc_rejects_wrong_receipt_type_and_symlink_escape \
  tests/test_a11_release_gate.py::test_repository_contract_pins_image_identity_tools_and_complete_slo_set
```

The drill uses temporary receipts and mocked image/tool evidence. It does not sign,
publish, deploy, shift traffic, mutate a remote ruleset or roll back a release.

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
