# A11 Release Candidate, Image Evidence and SLO Gate

Last verified SHA/date: pending the first clean-checkout execution of this runbook.

## Prerequisites

- A clean clone at the exact candidate SHA; no untracked or modified files.
- A backend image built from `uv.lock` with `SOURCE_REVISION` set to that SHA.
- Trivy image-level CycloneDX SBOM and JSON vulnerability report.
- A keyless cosign bundle issued to this repository's pinned security workflow.
- Content-free SLO measurements, all required gate receipts and the known-risk register.

The verifier reads no credentials, application content or private benchmark payloads. A
public-safe benchmark receipt may be referenced, but it must bind the exact candidate
SHA. Local fixture or dry-run receipts never stand in for provider, remote-ruleset or
production-operation evidence.

## Dry-run

Run the unit suite before producing evidence:

```bash
cd document-rag-platform/services/backend
.venv/bin/python -m pytest -q tests/test_a11_release_gate.py
```

Inspect tools without installing or claiming a scan:

```bash
command -v docker
command -v trivy || true
command -v cosign || true
```

Missing `trivy` or `cosign` is a failed/unavailable image gate, never a PASS.

## Exact commands

CI first builds and labels the image:

```bash
docker build --pull --no-cache \
  --build-arg SOURCE_REVISION="$GITHUB_SHA" \
  --tag "context-vault-backend:$GITHUB_SHA" \
  document-rag-platform/services/backend
```

After Trivy has created the image-level SBOM and scan report, bind their exact
hashes into the subject before signing it:

```bash
python scripts/a11_release_gate.py image-subject \
  --image-ref "context-vault-backend:$GITHUB_SHA" \
  --sbom reports/backend-image.sbom.cdx.json \
  --scan reports/trivy-backend.json \
  --json-output reports/backend-image.provenance.json
cosign sign-blob --yes \
  --bundle reports/backend-image.signature.bundle.json \
  reports/backend-image.provenance.json
```

Trivy creates `reports/backend-image.sbom.cdx.json` in CycloneDX format and
`reports/trivy-backend.json` in JSON format. Verify exact identity, digest, SBOM and
zero HIGH/CRITICAL findings:

```bash
python scripts/a11_release_gate.py image-verify \
  --subject reports/backend-image.provenance.json \
  --sbom reports/backend-image.sbom.cdx.json \
  --scan reports/trivy-backend.json \
  --signature-bundle reports/backend-image.signature.bundle.json \
  --certificate-identity "https://github.com/$GITHUB_WORKFLOW_REF" \
  --json-output reports/backend-image-security.json
```

Evaluate content-free measurements against the allowlisted SLO policy:

```bash
python scripts/a11_release_gate.py slo \
  --policy document-rag-platform/infra/release/slo-policy.json \
  --measurements /private/content-free-slo-measurements.json \
  --json-output /private/a11-slo-receipt.json
```

The checked-in policy is deliberately
`PROVISIONAL_BLOCKED_PENDING_BASELINE`: its initial targets are not operational
evidence. After the first content-free baseline and test-alert delivery have real
receipts, the owner may set `ACTIVE_BASELINE_BOUND` and record the exact baseline
receipt SHA-256. The evaluator rejects every unbound or provisional policy.

Finally, bind every receipt hash in a private RC manifest and evaluate it:

```bash
python scripts/a11_release_gate.py rc \
  --manifest /private/a11-rc-manifest.json \
  --known-risks document-rag-platform/infra/release/known-risks.json \
  --json-output /private/a11-rc-receipt.json
```

## Expected output

Each generated receipt is a new mode-0600 file. `image-verify` passes only when the
cosign identity is exact, the signed subject binds the exact SBOM and scan hashes,
Trivy's image ID equals the subject digest, the image SBOM's root component binds the
same ref/digest and HIGH/CRITICAL findings are zero. SLO output lists every owner,
escalation and runbook. RC output lists exactly eleven gates and one shared source
SHA/image digest.

## Stop conditions

- Dirty worktree, SHA or image digest drift.
- Missing scanner/signature binary or evidence.
- Empty/malformed SBOM, failed cosign identity verification, or any HIGH/CRITICAL CVE.
- Missing/stale SLO value, an unknown metric label, or any SLO breach.
- Missing gate, receipt hash drift, open/non-closed P0/P1 risk, remote ruleset failure,
  backup/restore failure or benchmark not bound to the exact candidate SHA.

On any SLO breach the receipt sets `feature_freeze_required=true`; reliability work
takes priority over feature work.

## Rollback

This runbook performs no deployment. Reject the candidate and retain the prior verified
image digest and restore point. If a canary was separately deployed, shift traffic back
to the prior digest. Never force a schema downgrade; use the verified restore/cutover
path in `release-rollback.md`.

## Evidence / receipt

Retain the image subject, image-level SBOM, Trivy JSON, keyless cosign bundle, image
security receipt, SLO receipt, RC manifest/receipt and their SHA-256 sums. Public
artifacts contain no credential, raw object key, prompt, document text or private golden
data. A CI artifact alone does not authorize release; human release authority remains
required.
