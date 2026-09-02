# CV3 Baseline Security Exposure Summary

This inventory records paths and hashes only; it does not reproduce endpoint,
credential, certificate subject, or file-content values.

## Public-tree candidates requiring later policy resolution

- `document-rag-platform/.env.example`
- `document-rag-platform/services/backend/src/config.py`
- `document-rag-platform/services/backend/Dockerfile`
- `document-rag-platform/services/backend/ttroot-g3.crt`

Certificate file SHA-256:
`d83cd3f0daeadd01889a1b5a90cdde1630e26017a37afeaf180bfb7e6fa34ca2`.
The certificate path occurs in one reachable commit. No provenance or removal
decision is inferred during bootstrap.

## GitHub control-plane observation

- Repository visibility: public
- Branches observed: `main` only
- Rulesets: none
- `main` branch protection: disabled
- Open pull requests/issues/releases: none observed
- Repository Actions secret names: none returned
- Repository Actions variable names: none returned
- Environments: none returned

The latest remote security workflow failed at its secret-scan step. This was
rechecked locally using official Gitleaks `v8.30.1`; the release asset matched its
published SHA-256 checksum. Initial history and working-tree scans found the same
three synthetic values in `tests/test_redaction.py`. Their rule, line, function,
literal length, and literal hash were reviewed without exposing the values.

`.gitleaksignore` now pins only the six exact history/current fingerprints for
those redaction fixtures. It does not allowlist the file, directory, or rules.
After that narrow classification:

- Full Git history scan: exit `0`, findings `0`
- Current working-tree scan: exit `0`, findings `0`
- Redacted private report SHA-256:
  `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- `.gitleaksignore` SHA-256:
  `a6c24ae15b61fef880f7021d85fcb94095183f1b1f8e8f9189bb25574cf4f4f9`

The bootstrap audit directory is additionally checked by
`scripts/verify_baseline.py`, which reports rule and path only and never emits
matching content. Bootstrap secret-scan admission result: `PASS`. Certificate
and endpoint provenance remain later-stage remediation items, not silently
accepted production defaults.
