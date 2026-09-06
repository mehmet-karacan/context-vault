# Approved real-provider benchmark tier

This tier collects candidate evidence for the real-provider quality gate. It must
run only with an owner-reviewed v2 or v3 private-pack manifest, explicit
`--approval-manifest`, separately approved embedding and generation
provider/model identities, credentials, budget and compatible
data classification. The manifest is a reference to an existing human
authorization, not authority to invent one. No real provider run has been
authorized or verified by this code change.

Use the backend's locked dev environment (`uv sync --locked --group dev`); manifest
validation uses the versioned JSON schema with format checking. Malformed fields,
missing review/date/hash, pending review and additional fields fail before runner
dispatch. Schema validity alone does not establish provider policy or budget approval.

The v2 provider report must contain `schema_version=2.0` and nonblank
`embedding_provider`, `embedding_model`, `generation_provider` and
`generation_model` values. It must also contain SHA-256 profile/prompt/config
hashes, nonnegative integer safety counters, finite [0,1] Recall@5, MRR@10,
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
release decision. The wrapper can bind a separately issued golden non-transfer
receipt to exact evidence bytes, as documented below, but cannot authenticate the
reviewer or make the final admission. Numeric comparison or receipt binding alone
neither seals a baseline nor grants release.

The runtime version-selects the frozen `private-pack-manifest-v2.schema.json` or
`private-pack-manifest-v3.schema.json`, both of whose `review_status` is exactly
`approved`, and
`benchmark-approval-manifest-v3.schema.json`, whose `decision` is exactly
`approved`. The approval binds the private-manifest file hash and
dataset/classification to both provider/model identities, runner bundle hash,
environment hash, expiry and maximum duration/calls/input tokens/output tokens/USD.
The wrapper passes those caps to the approved runner, enforces its timeout and
rejects reported usage above them. This is defense in depth: a malicious runner
could spend before reporting, so the exact runner bundle still requires human
review and provider-side budget limits. Provider/model/environment values can only
be compared after output exists; other approval bindings fail before dispatch.
The v3 manifest additionally binds the golden file, label-free execution file and
normalized execution projection as three separate SHA-256 values. A v3 run requires
an already-running external capture socket plus a one-time nonce supplied through a
bounded, non-symlink, mode-`0600` file. The wrapper forwards the socket and nonce only
to the approved child environment; neither value nor either path is projected into
the report. V2 remains accepted for historical and non-capture runs, but capture
arguments are rejected with v2. Older private-manifest v1 and approval v1/v2 schemas remain for historical
evidence validation only and are not accepted by the runtime. Only explicitly
named credential variables reach the child; ambient HOME, Codex,
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

`--baseline <report> --baseline-seal <seal>` requires a v2 human-approved seal
validated by `benchmark-baseline-seal-v2.schema.json`; its report hash and both
provider/model identities plus dataset/profile/prompt/config/environment provenance
must match. The baseline report itself must be v2. Current and baseline provenance
must also match before numeric regression comparison. The wrapper reads and
validates the exact baseline and seal bytes before provider dispatch, then compares
the current report against that in-memory binding. `--strict` changes a
warning-bearing candidate to FAIL; it
does not affect clean contract/offline tiers. Neither an approval manifest nor a
baseline seal may be created by the model/CLI itself. Frozen legacy schemas remain
in the tree for historical evidence validation only; the runtime does not upgrade
or accept a legacy private manifest, approval, report or seal.

## Approval preparation (no provider effect)

Prerequisites: use the same locked backend Python, machine/runtime and exact direct
runner path intended for the benchmark. The v2 or v3 private-pack manifest must already
have completed owner review and exact `review_status=approved`. Keep the manifest
and both outputs outside the repository;
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
revision. It deliberately omits approval identity/times, the four provider/model
identity values, credentials and budgets, so it is not an approval manifest. Stop
if validation fails, the pack is
not owner-reviewed, any hash is unexpected, or the intended runner/environment is not
the one independently reviewed.

An authorized human uses those fingerprints, the versioned approval schema and
separately approved embedding and generation provider/model identities,
credential-variable names, expiry and provider-side budgets to issue the approval
manifest. Validate it without provider
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

For v3, create the nonce file under a restrictive umask with exact mode `0600`,
then start `scripts/capture_local_provider_boundary.py` as a separate verifier
process before starting the benchmark. Bind its required arguments to the reviewed
source, runner, manifest, combined/split dataset, projection,
environment and four provider/model hashes. The collector exclusively creates the
evidence output, acknowledges every request boundary, seals the ordered ledger and
only then opens the golden file. The nonce is never sent over the socket: it keys a
mutual challenge/HMAC handshake and a single persistent, sequence-bound session, so
pathname replacement cannot impersonate the collector between admission and model
calls. Never put the nonce value on a command line, and
never reuse its file, socket or evidence path. Capture socket and nonce-file
arguments are execution-only; approval preflight/check modes reject them.

Example shape only (paths remain outside public evidence when private):

For a v3 run, create one fresh nonce and start the bound collector in a separate
terminal before invoking the wrapper. Use a short real directory such as
`/private/tmp` on macOS (not the `/tmp` symlink), and copy every hash/identity
exactly from the approved v3 manifest, approval preflight and approval. The
evidence output must be a new private path. These values are illustrative
placeholders, not authority:

```sh
umask 077
cv_capture_nonce="/private/path/a9-capture.nonce"
openssl rand -hex 32 >"$cv_capture_nonce"
chmod 600 "$cv_capture_nonce"

cv_capture_socket="/private/tmp/cv-a9-capture.sock"
cv_capture_evidence="/private/path/a9-request-capture.json"
cv_golden_dataset="/private/path/labels/golden.jsonl"

"$cv_python" scripts/capture_local_provider_boundary.py \
  --socket "$cv_capture_socket" \
  --nonce-file "$cv_capture_nonce" \
  --golden-dataset "$cv_golden_dataset" \
  --evidence-output "$cv_capture_evidence" \
  --repository-revision "$CV_APPROVED_SOURCE_REVISION" \
  --runner-bundle-sha256 "$CV_APPROVED_RUNNER_BUNDLE_SHA256" \
  --private-pack-manifest-sha256 "$CV_APPROVED_PRIVATE_MANIFEST_SHA256" \
  --dataset-sha256 "$CV_APPROVED_DATASET_SHA256" \
  --golden-dataset-sha256 "$CV_APPROVED_GOLDEN_SHA256" \
  --execution-dataset-sha256 "$CV_APPROVED_EXECUTION_SHA256" \
  --execution-projection-sha256 "$CV_APPROVED_EXECUTION_PROJECTION_SHA256" \
  --environment-hash "$CV_APPROVED_ENVIRONMENT_HASH" \
  --embedding-provider "$CV_APPROVED_EMBEDDING_PROVIDER" \
  --embedding-model "$CV_APPROVED_EMBEDDING_MODEL" \
  --generation-provider "$CV_APPROVED_GENERATION_PROVIDER" \
  --generation-model "$CV_APPROVED_GENERATION_MODEL"
```

The collector must remain running until it writes the mode-`0600` evidence and
exits after the seal. Do not reuse its socket, nonce or evidence output. The real
benchmark invocation below must use the same socket and nonce file. An absent,
replaced, unauthenticated or rejecting collector fails before the corresponding
local model call; later pathname replacement cannot move the already authenticated
persistent session.

```sh
python scripts/run_eval.py --tier real-benchmark --strict \
  --approval-manifest "$APPROVAL_MANIFEST" \
  --private-pack-manifest "$PRIVATE_PACK_MANIFEST" \
  --provider-runner "$APPROVED_RUNNER" \
  --capture-socket "$CAPTURE_SOCKET" \
  --capture-nonce-file "$CAPTURE_NONCE_FILE" \
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

## Independent golden-data non-transfer receipt (no provider effect)

After a real-benchmark candidate exists, an independent human/verifier must inspect
the actual provider-request boundary. The verifier—not this CLI, runner, model or
automation—issues a receipt conforming to
`benchmark-golden-non-transfer-receipt-v1.schema.json`. Accepted methods are an
independent request capture, independent egress observation or provider audit log.
The evidence artifact may be private. For `independent-egress-observation` and
`provider-audit-log`, the CLI treats it as opaque bytes and only hashes it. For
`independent-request-capture`, it must conform to
`local-provider-capture-evidence-v1.schema.json`; that schema contains only hashes,
bounded counts, provider/model identities, UTC timestamps and explicit false
retention flags. Raw prompts, responses, golden payloads and paths are forbidden by
the closed schema and are never projected by the checker.

The receipt binds the exact source commit, runner source bundle, private-pack
manifest and bundle, golden-label file, label-free execution file and normalized
execution projection, final wrapper report,
execution environment and both embedding/generation provider-model identities. Its
verification time must follow the candidate time, its request count must cover every
manifest record and the candidate must contain the runner's explicit
`golden_results_sent_to_provider=false` assertion. The independent evidence still
has to prove that assertion; the runner cannot prove its own non-transfer behavior.
For request capture, the checker also requires the candidate's capture session,
ordered-ledger and request-count fields to exactly match the evidence; request sealing
must precede the first golden-file open, which must precede the candidate and receipt
timestamps.

Keep every input and output outside Git and under the applicable private evidence
policy. Use a new output path; the checker refuses symlinks and overwrites and creates
the bounded result with mode `0600`:

```sh
cv_receipt="/private/path/golden-non-transfer-receipt.json"
cv_candidate_report="/private/path/real-benchmark-report.json"
cv_golden_dataset="/private/path/labels/golden.jsonl"
cv_execution_dataset="/private/path/execution/cases.jsonl"
cv_independent_evidence="/private/path/independent-request-capture.json"
cv_receipt_check="/private/path/golden-non-transfer-check.json"

"$cv_python" scripts/run_eval.py --check-golden-non-transfer-receipt \
  --golden-non-transfer-receipt "$cv_receipt" \
  --candidate-report "$cv_candidate_report" \
  --private-pack-manifest "$cv_private_manifest" \
  --golden-dataset "$cv_golden_dataset" \
  --execution-dataset "$cv_execution_dataset" \
  --non-transfer-evidence "$cv_independent_evidence" \
  --provider-runner "$cv_candidate_runner" \
  --json-output "$cv_receipt_check"
```

Expected status is `RECEIPT_BOUND_TO_EXACT_CANDIDATE` with
`receipt_binding_verified=true`, `provider_invoked=false`,
`receipt_authority_verified=false` and `release_gate_eligible=false`. The last two
fields are deliberate: schema and hash validation cannot establish who controls the
reviewer identity, and a non-transfer receipt does not replace baseline sealing,
quality/security review or the final human release decision. Any byte, hash,
timestamp, source, model or environment mismatch fails closed. The CLI validates a
receipt; it never generates one.

The aggregate `verify_release` step therefore requires both the successful
human-sealed regression candidate and this separately checked non-transfer receipt.
It rejects a benchmark runner or receipt checker that sets its own
`release_gate_eligible=true`; promotion belongs to the independent aggregate step
after every exact-SHA component is present. The final merge/tag/release decision
still remains a separate human authority gate.

## Local BGE runtime admission

`BAAI/bge-m3` may be admitted as the embedding side of a local benchmark only
when an exact local Hugging Face snapshot is supplied. The optional runtime is
kept out of normal backend/CI installs:

```sh
cd document-rag-platform/services/backend
uv sync --locked --all-groups --extra local-eval
```

The verifier hashes every resolved snapshot file, rejects cache-root symlink
escapes, loads with Hugging Face/Transformers offline guards and
`local_files_only=True`, performs real normalized inference, then repeats the
full snapshot validation to detect byte or symlink drift during inference. Its
JSON output is created as a new mode-0600 file and is never overwritten:

```sh
.venv/bin/python ../../../scripts/verify_local_bge.py \
  --snapshot /local/huggingface/models--BAAI--bge-m3/snapshots/<revision> \
  --expected-model BAAI/bge-m3 \
  --expected-revision <revision> \
  --expected-bundle-sha256 <sha256> \
  --device cpu \
  --json-output /new/private/path/local-bge-runtime.json
```

The report binds the repository revision, verifier bytes, dependency lock and
model bundle. `library_offline_mode=true` is not an operating-system network
sandbox; the report therefore retains `network_isolation_verified=false`.
Run the benchmark host under an independently observed egress deny policy when
that stronger claim is required.

BGE-M3 is an embedding model, not a generation model. A passing local-BGE
runtime report cannot satisfy the approved real-provider tier by itself. The
approval, provider report and baseline seal must separately bind the exact
embedding and generation providers/models before the first real benchmark can
be sealed.

## Local generation runtime admission

The MacBook-local generation-side smoke uses an exact cached
`Qwen/Qwen2.5-1.5B-Instruct` snapshot. This is a controlled prompt-adherence
and runtime-integrity check, not a RAG quality benchmark. It proves only that
the admitted bytes load without remote provider execution and deterministically
return the exact opaque token for a bounded context case plus exact `NO_CONTEXT`
for a bounded no-answer case. It does not prove general Turkish answer quality,
grounding, citation quality or release eligibility.

Install the locked optional runtime and run the verifier with a new private
output path:

```sh
cd document-rag-platform/services/backend
uv sync --locked --all-groups --extra local-eval

.venv/bin/python ../../../scripts/verify_local_generation.py \
  --snapshot /local/huggingface/models--Qwen--Qwen2.5-1.5B-Instruct/snapshots/<revision> \
  --expected-model Qwen/Qwen2.5-1.5B-Instruct \
  --expected-revision <revision> \
  --expected-bundle-sha256 <sha256> \
  --device mps \
  --timeout-seconds 600 \
  --json-output /new/private/path/local-generation-runtime.json
```

The verifier limits the snapshot to 64 files and 4,000,000,000 resolved bytes,
allows at most 4,096 total prompt tokens and 72 generated tokens, and enforces a
maximum 600-second subprocess timeout. The worker pipe capability prevents an
accidental direct CLI worker-mode bypass in the trusted-local operator model; it
is not authentication against a malicious process running as the same OS user.
Snapshot path, revision, symlink targets and bytes are checked before and after
inference. Only exact output hashes and pinned runtime versions enter the report;
raw prompts and generation text are not retained. Library offline mode is not an
OS egress sandbox, so `network_isolation_verified` remains false.

The smaller `Qwen/Qwen2.5-0.5B-Instruct` candidate was rejected after real CPU
smokes returned unsafe/unsupported answers. An initial natural-language smoke of
the 1.5B candidate also failed the semantic guards. The final opaque-token case
only admits the execution contract; those failures must not be reinterpreted as
quality success. Cached owner/name/revision and bundle hashes establish local byte
identity, not cryptographic proof of publisher origin or license provenance.

Model weights remain outside Git. A passing local generation report still needs
an owner-reviewed private pack, a production-path run using both exact local
models, independent request/non-transfer evidence, and a human baseline seal
before A9 can close.
