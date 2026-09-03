# Approved real-provider benchmark tier

This tier collects candidate evidence for the real-provider quality gate. It must
run only with an owner-reviewed private-pack manifest, explicit
`--approval-manifest`, approved provider/model, credentials, budget and compatible
data classification. The manifest is a reference to an existing human
authorization, not authority to invent one. No real provider run has been
authorized or verified by this code change.

Use the backend's locked dev environment (`uv sync --locked --group dev`); manifest
validation uses the versioned JSON schema with format checking. Malformed fields,
missing review/date/hash, pending review and additional fields fail before runner
dispatch. Schema validity alone does not establish provider policy or budget approval.

The provider report must contain nonblank provider/model, SHA-256 profile/prompt/
config hashes, nonnegative integer safety counters, finite [0,1] Recall@5, MRR@10,
citation precision/coverage and nonnegative ordered latency percentiles. Missing,
bool, NaN, infinite or out-of-range values do not pass. A sealed-baseline
comparison additionally requires positive end-to-end p95 values; missing comparator
metrics fail too.
Only the bounded report contract is projected; arbitrary raw runner fields cannot
overwrite the CLI's exact revision/tier or leak prompts into the report. Child
stdout/stderr are not replayed into public logs. Real output still belongs in the
private evidence location; the wrapper cannot guarantee arbitrary identifiers are public-safe.

Golden answers and expected sources must never be included in provider requests.
The wrapper cannot observe that fact: an absent runner assertion stays `null`,
and a reported `true` fails. Even a reported `false` is not independent verification.
`quality_claim=runner-reported-unverified`, `baseline_review_required=true` and
`release_gate_eligible=false` distinguish validated report shape from actual quality
or an approved baseline. A matching approved baseline can make
`regression_candidate_eligible=true`; it still cannot make release eligibility true.
Human review/seal and independent provider/request evidence are required before a
release decision; this wrapper does not implement that final admission. Numeric
comparison alone neither seals a baseline nor grants release.

The versioned `benchmark-approval-manifest.schema.json` binds the private-manifest
file hash and dataset/classification to provider/model, runner bundle hash,
environment hash, expiry and maximum duration/calls/input tokens/output tokens/USD.
The wrapper passes those caps to the approved runner, enforces its timeout and
rejects reported usage above them. This is defense in depth: a malicious runner
could spend before reporting, so the exact runner bundle still requires human
review and provider-side budget limits. Provider/model/environment values can only
be compared after output exists; other approval bindings fail before dispatch.
Only explicitly named credential variables reach the child; ambient HOME, Codex,
session and unrelated credentials are not inherited. The environment hash is
computed before dispatch from allowlisted Python/OS/machine/lockfile properties and
then compared with approval and report. The command must be one direct, existing,
hashed executable entrypoint; `python -m` and `sh -c` chains are rejected. Its wider
dependency closure must be represented by the approved environment hash.

The required quality report covers retrieval ranks (including first-relevant), context, duplicate/version/
profile/tenant leakage, identifier, answerability, citation, unsupported-claim,
sufficiency, contradiction, prompt-injection, retry/duplicate/orphan counts,
ingestion/retrieval/end-to-end and queue/stage p50/p95/p99 latency, error
distribution and provider calls/tokens/cost. Percentiles must be ordered. Query-type
rows must exactly cover and total the private manifest. Missing, non-finite,
out-of-range or oversized fields fail closed.

`--baseline <report> --baseline-seal <seal>` requires a versioned human-approved
seal whose report hash and provider/model/dataset/profile/prompt/config/environment
provenance match. Current and baseline provenance must also match before numeric
regression comparison. `--strict` changes a warning-bearing candidate to FAIL; it
does not affect clean contract/offline tiers. Neither an approval manifest nor a
baseline seal may be created by the model/CLI itself.

Example shape only (paths remain outside public evidence when private):

```sh
python scripts/run_eval.py --tier real-benchmark --strict \
  --approval-manifest "$APPROVAL_MANIFEST" \
  --private-pack-manifest "$PRIVATE_PACK_MANIFEST" \
  --provider-runner "$APPROVED_RUNNER" \
  --baseline "$APPROVED_BASELINE" --baseline-seal "$BASELINE_SEAL" \
  --json-output "$PRIVATE_REPORT_JSON" --markdown-output "$PRIVATE_REPORT_MD"
```

An initial candidate omits both baseline arguments and should omit `--strict`; its
expected human-seal warning makes strict mode fail while preserving the report for
review. After human review its exact report may be referenced by a separately issued
seal. A later regression with that valid seal and an explicit false golden-transfer
assertion may pass strict validation, but remains only an independently reviewable
candidate. Do not put any of these
private paths, credentials, runner output or raw provider payloads into Git.
