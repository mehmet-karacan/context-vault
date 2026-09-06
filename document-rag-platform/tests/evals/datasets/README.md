# Evaluation dataset provenance

The evaluation tiers do not share one implicit dataset. Each report identifies the
input it actually executed:

- `contract-smoke` executes the backend `tests/evals/datasets/golden.jsonl` file
  and reports that file's SHA-256 with provenance kind `contract-golden-file`.
- `offline-e2e` executes the complete `offline_e2e` test directory plus fixed
  production-pipeline targets whose cases are defined in test fixtures. Its
  `dataset_sha256` is therefore `null`; the report instead records a deterministic
  `inline-test-fixtures` source-bundle hash over the discovered tests, fixed targets,
  `tests/conftest.py` and `pyproject.toml`.
- `real-benchmark` takes its dataset hash only from an owner-reviewed v2
  private-pack manifest with exact `review_status: approved` and reports
  provenance kind `private-manifest-declared`. The v1 schema is historical-only.

The committed `public-synthetic-v2.jsonl` corpus is reviewable test material, not a
fallback identity for another tier. Its manifest is bound to a versioned schema,
the exact file hash, record count, dataset version and split set. CI checks those
bindings without invoking a provider:

```sh
document-rag-platform/services/backend/.venv/bin/python scripts/run_eval.py \
  --public-dataset-status \
  --json-output /tmp/context-vault-public-dataset-status.json
```

An integrity result of `PASS` does not mean that an owner reviewed the examples or
that a release gate passed. Until an authorized human performs the review, the
manifest must retain `review_status: pending`, reviewer
`PENDING_OWNER_REVIEW`, and null review time and receipt. The status command then
reports `receipt_binding_verified: false`, `review_complete: false`,
`release_gate_eligible: false` and `provider_invoked: false`.

When the dataset bytes change, update its semantic `dataset_version`, exact SHA-256,
record count and split set. Reset the review fields to the pending values above until
the changed bytes are reviewed again. An approved manifest claim requires a
non-pending human reviewer, a non-future UTC timestamp and the SHA-256 of an
independently retained review receipt. That external receipt follows
`public-dataset-review-receipt.schema.json`; automation must not create or approve
it. Its exact byte hash is copied into the manifest only after the review decision.
The receipt remains outside the repository when its policy or reviewer reference is
private.

The no-effect status command can verify the receipt's schema, exact byte hash and
dataset/version/count/split/reviewer/time bindings:

```sh
document-rag-platform/services/backend/.venv/bin/python scripts/run_eval.py \
  --public-dataset-status \
  --public-review-receipt /secure/path/public-dataset-review-receipt.json \
  --json-output /tmp/context-vault-public-dataset-status.json
```

An exact match reports `receipt_binding_verified: true`. It still reports
`review_authority_verified: false` and `review_complete: false`, because this local
validator cannot authenticate the human or grant owner authority. Without an
external authority decision, an approved claim remains either
`manifest-declared-approved-unverified` or
`receipt-bound-approved-unverified-authority`. Do not manufacture those fields or
the receipt from automation or model output. Public-dataset approval still does not
substitute for the private pack,
provider/budget authorization, real run evidence or sealed baseline required by the
real benchmark gate.
