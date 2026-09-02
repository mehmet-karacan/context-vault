# Approved real-provider benchmark tier

This tier collects candidate evidence for the real-provider quality gate. It must
run only with an owner-reviewed private-pack manifest, explicit `--approval-id`,
approved provider/model, credentials, budget and compatible data classification.
The CLI argument is a reference to an existing human authorization, not authority
to invent one. No real provider run has been authorized or verified by this code change.

Use the backend's locked dev environment (`uv sync --locked --group dev`); manifest
validation uses the versioned JSON schema with format checking. Malformed fields,
missing review/date/hash, pending review and additional fields fail before runner
dispatch. Schema validity alone does not establish provider policy or budget approval.

The provider report must contain nonblank provider/model, SHA-256 profile/prompt/
config hashes, nonnegative integer safety counters, finite [0,1] Recall@5, MRR@10,
citation precision/coverage and positive p95 milliseconds. Missing, bool, NaN,
infinite or out-of-range values do not pass. Missing comparator metrics fail too.
Only the bounded report contract is projected; arbitrary raw runner fields cannot
overwrite the CLI's exact revision/tier or leak prompts into the report. Child
stdout/stderr are not replayed into public logs. Real output still belongs in the
private evidence location; the wrapper cannot guarantee arbitrary identifiers are public-safe.

Golden answers and expected sources must never be included in provider requests.
The wrapper cannot observe that fact: an absent runner assertion stays `null`,
and a reported `true` fails. Even a reported `false` is not independent verification.
`quality_claim=runner-reported-unverified`, `baseline_review_required=true` and
`release_gate_eligible=false` distinguish validated report shape from actual quality
or an approved baseline. Human review/seal and independent provider/request evidence
are still required before a release decision; this wrapper does not implement that
final admission. Numeric comparison alone neither seals a baseline nor grants release.
