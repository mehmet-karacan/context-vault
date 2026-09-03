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

## Approval preparation (no provider effect)

Prerequisites: use the same locked backend Python, machine/runtime and exact direct
runner path intended for the benchmark. The private-pack manifest must already have
completed owner review. Keep the manifest and both outputs outside the repository;
the commands do not read credential values or execute the runner. Each JSON output
must resolve outside the repository and be a new path; preparation uses exclusive,
no-symlink creation with mode `0600` and refuses to overwrite any existing file.

```sh
cv_python="document-rag-platform/services/backend/.venv/bin/python"
cv_private_manifest="/private/path/private-pack-manifest.json"
cv_candidate_runner="/private/path/provider-runner"
cv_preflight_output="/private/path/approval-preflight.json"

"$cv_python" scripts/run_eval.py --approval-preflight \
  --private-pack-manifest "$cv_private_manifest" \
  --provider-runner "$cv_candidate_runner" \
  --json-output "$cv_preflight_output"
```

Expected status is `HUMAN_APPROVAL_REQUIRED` with `provider_invoked=false` and
`credential_values_read=false`. The bounded file contains only the exact private-manifest,
dataset, runner-bundle and environment hashes/classification plus tool/repository
revision. It deliberately omits approval identity/times, provider/model, credentials
and budgets, so it is not an approval manifest. Stop if validation fails, the pack is
not owner-reviewed, any hash is unexpected, or the intended runner/environment is not
the one independently reviewed.

An authorized human uses those fingerprints, the versioned approval schema and
separately approved provider/model, credential-variable names, expiry and
provider-side budgets to issue the approval manifest. Validate it without provider
dispatch before any real run:

```sh
cv_human_approval="/private/path/benchmark-approval-manifest.json"
cv_approval_check="/private/path/approval-check.json"

"$cv_python" scripts/run_eval.py --check-approval \
  --approval-manifest "$cv_human_approval" \
  --private-pack-manifest "$cv_private_manifest" \
  --provider-runner "$cv_candidate_runner" \
  --json-output "$cv_approval_check"
```

Expected status is `APPROVAL_VALID_FOR_CURRENT_INPUTS`, again with
`provider_invoked=false` and `credential_values_read=false`. Any manifest, runner path/byte,
lock/runtime/machine, dataset/classification or expiry drift is a stop condition and
requires a fresh preflight plus human approval. Retain private preflight/check/approval
files under the applicable private evidence policy; never commit them or their raw
paths. A successful check only validates binding—it does not authorize the CLI/model
to choose a provider, spend budget, execute the run or seal a baseline.

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
