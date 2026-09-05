# A11 Backup/DR and Operations Evidence

This package records a bounded disposable-environment rehearsal. It used one
synthetic encrypted object and a matching database registry row. It did not read,
delete, restore over, or otherwise mutate user data.

`DR_RECEIPT.json` preserves the earlier rehearsal result but is explicitly
invalidated by the subsequent safety audit: its manifest and target-admission
contract predates immutable container/cluster identity binding and cannot prove the
hardened implementation. Raw object keys, credentials and private endpoint paths are excluded. The
private restore material is outside Git with directory mode `0700` and file mode
`0600`; it is governed by the documented retention policy.

`DOCTOR_RECEIPT.json` preserves the earlier disposable orphan observation, but is
also invalidated because it predates the secret-free projection, exact service-label
resolution and local-Docker admission hardening. Resource names are represented
only by hashes.

Limits and open gates:

- The old restore result is superseded and is not evidence for the hardened code.
- This is synthetic local evidence, not a production restore or release approval.
- The pre-existing local Context Vault database was checked read-only and reported
  migration head `cv3_00000003` rather than expected `cv3_00000006`; Redis was not
  available in that source stack. No source repair was attempted.
- Backup scheduling/alert delivery and production key escrow were documented but
  were not exercised.
- The receipt is explicitly not release-gate eligible and does not replace a human
  release decision.

The evaluated runtime source revision was
`fcddb4a49e99c05e7f225fff64c56c0a778fb970`. Focused tests were subsequently run
against base revision `398b503ade63aea7d97fda29ac60dc6924de406a` plus the
uncommitted A11 files listed in `SOURCE_MANIFEST.json`.
