# Structured answer and citation runbook

## Runtime checks

1. Confirm migration head is `cv3_00000007` (structured-answer persistence was
   introduced by `cv3_00000006`) and run
   `scripts/verify_migrations.py --strict` against the intended database.
2. Confirm `OBJECT_STORAGE_ENCRYPTION_KEY` resolves to exactly 32 bytes.
3. Keep `PROVIDER_REQUEST_RETENTION=none` unless an approved policy explicitly
   allows metadata-only retention.
4. Confirm the selected documents' immutable policy records permit remote
   generation before using a remote chat adapter.

## Expected flow

`ContextBundle.selected_items` is assigned `[S1..Sn]` labels without splitting
a label from its content. The provider returns `AnswerEnvelope`. Dynamic label,
claim/text and used-label checks run before response or persistence. Only then
are the user message, assistant message, claims, citations and claim links
written in one database transaction.

One schema/grounding repair is allowed. Unknown labels, source-less claims or a
second malformed result terminate as `malformed_response`. Timeouts and partial
streams terminate as `provider_failure`. Neither path writes citations.

## Incident triage

- Never log the prompt or raw provider body. Correlate with request id,
  retrieval-run id, prompt hash, model and fixed-cardinality reason.
- For a citation dispute, compare `evidence_hash`, `content_hash`, locator,
  version/profile and prompt hash. Decrypt `evidence_snapshot_encrypted` only
  under an authorized support workflow.
- A deleted/reindexed chunk may leave `chunk_id` null. This is expected when the
  encrypted snapshot and hashes remain intact.
- Do not attach a legacy conversation with null principal provenance to a user.
  Resolve ownership explicitly in a reviewed migration.

## Retention

New evidence snapshots receive `evidence_expires_at` from
`ANSWER_EVIDENCE_RETENTION_DAYS` (default 30). Expiry makes the snapshot eligible
for a separately audited purge; claim/hash/locator provenance remains. Messages
use soft deletion so claim/citation relationships are not cascaded by an API
delete. Legal holds and the ingestion GC citation gate take precedence.
