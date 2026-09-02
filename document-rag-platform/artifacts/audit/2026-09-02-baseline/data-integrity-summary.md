# CV3 Baseline Data Integrity Summary

## Current verification result

`PASS_SYNTHETIC_NEW_LINEAGE`

No legacy Context Vault database or object-store runtime was reachable. After
explicit owner authorization, a blank isolated V3 runtime was verified with
synthetic relational and object data. This does not claim legacy-data recovery.

- Source/restore project, document, version and chunk counts: `1/1/1/1`.
- Source/restore migration revision: `cv3_00000001`.
- Cross-document/version chunk violations: `0` for the admitted fixture.
- Active embedding profiles: `1`; null `config_hash`: `0`.
- Source/restore object inventory: `1/1`; byte hash equal.

## Historical, unverified observations

The committed 2026-08-31 audit reports two chunks whose version differs from the
document active version, an active profile without `config_hash`, one unsupported
extensionless source, and an orphan migration container. These are reproduction
targets, not current facts.

## Safety result

- Legacy runtime database writes: none
- Legacy runtime object-store writes/deletes: none
- Isolated synthetic database/object writes: performed and restore-verified
- Container or volume deletion: none
- Data reset, stamp, downgrade, or repair: none

This admission gate is `PASS` for the new empty V3 lineage. If a legacy source
is discovered later it remains read-only until separately fingerprinted and
admitted.
