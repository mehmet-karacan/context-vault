# ADR-011: Structured answers and claim-level citation provenance

- Status: Accepted
- Date: 2026-09-02
- Owner: Mehmet KARACAN
- Decision evidence: migration `cv3_00000006`, A8 PostgreSQL integration and
  structured-answer adversarial tests

## Decision

Generation has one accepted output: the strict `AnswerEnvelope` JSON schema.
Every answerable response contains claims whose source labels are a subset of
the current `ContextBundle`. Claim text must occur in the answer text and the
ordered union of claim labels must equal `used_source_labels`. A malformed,
source-less or unknown-label response receives one content-free repair attempt;
failure becomes a typed no-answer and is never persisted as an answer.

Only `ContextBundle.selected_items` are packaged. Query, policy, untrusted
conversation history and untrusted source evidence use distinct delimiters.
The adapter has no tools. Remote generation is blocked unless every selected
item's immutable content-policy decision permits it.

`message_claims` stores validated claims and `claim_citations` stores ordered
many-to-many claim/source relationships. `message_citations` stores only labels
the model declared and validation accepted. It snapshots retrieval, fusion and
reranker scores, locator, version/profile/model/prompt/config hashes, evidence
hash and an AES-256-GCM encrypted bounded excerpt. The excerpt gets a retention
deadline; public logs receive neither prompt, query, response nor excerpt.

Conversation reads require exact principal, workspace, project and conversation
scope. History is bounded and explicitly untrusted; previous assistant output
is never a canonical fact. Generated title fields remain `unset` until a future
validated title producer records model and prompt provenance. Message deletion
is soft (`deleted_at`) so citation integrity is retained; source GC may null
foreign keys but cannot erase the immutable citation hash/snapshot.

## Provider retention

The application requests `PROVIDER_REQUEST_RETENTION=none` by default and does
not locally log raw requests or responses. A deployment may select
`metadata_only`; enabling provider-side content retention requires a separate
data-policy review and is not implied by this setting.

## Consequences

- Gateways must support strict JSON-schema response format.
- Legacy conversations without principal provenance stay preserved but fail
  closed until an owner is explicitly resolved; migration never invents one.
- Provider failure, malformed response, policy refusal, insufficient evidence,
  smalltalk and permission denial remain distinguishable terminal reasons.
