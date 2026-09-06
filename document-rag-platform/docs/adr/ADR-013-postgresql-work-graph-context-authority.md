# ADR-013: PostgreSQL Work Graph and bounded context authority

- Status: Accepted locally
- Date: 2026-09-06
- Owner: Mehmet KARACAN
- Supersedes: Markdown, transcript, Git history, and vector projections as
  operational authority
- Decision evidence: migration `cv3_00000007`, A12 Work Graph, compiler,
  registry, adapter, and persistence tests

## Decision

PostgreSQL is the sole operational authority for work items, attempts, claims,
fencing tokens, state transitions, approvals, and receipts. An effect may start
only after an exact scope and request intent is committed under a live claim.
Prepare is read-only. Revision drift, an expired lease, a stale fencing token,
an approval mismatch, or an idempotency-request mismatch fails closed.

Completion requires an immutable `apply → verify → close` receipt chain and
acceptance evidence. Database triggers protect append-only events/receipts and
the terminal transition even when application code is bypassed. A process death
or uncertain effect is reconciled; it is never inferred to be successful.

Durable knowledge is separately versioned. Model or transcript output can only
be proposed. Review, approval, supersession, revocation, expiration, and conflict
resolution require policy-authorized human or policy actors and preserve actor,
time, source, evidence, and predecessor provenance.

`PROJECT_CONTEXT.md` and `.contextvault/project.yaml` identify a project. The
machine manifest is strictly validated and machine-generated payloads are
hash-bound. These files describe and project policy; they do not replace Work
Graph state.

The ContextCompiler loads mandatory security and active-work inputs first,
forbids full-vault preload, applies provider data classification and local-only
rules, excludes stale or superseded knowledge, and binds the selected core to an
immutable manifest hash and attempt. OpenCode, Claude, and Codex adapters may
format that core but may neither change its semantic hash nor write canonical
state directly.

Provider, model, and skill routing uses exact version/config/package hashes,
capability, data policy, health, benchmark, and permission scope. A fallback may
not silently change an embedding profile. Mutable skill refs and package-hash
drift fail admission.

## Consequences

- CLI and API composition must resolve actor, project, claim, provider/model,
  skill policy, and knowledge heads from canonical records rather than trusting
  a single self-asserted request.
- Retrieval indexes, Markdown/Obsidian views, analytics, and transcripts remain
  reconstructable or derived projections.
- Real mutations use typed, path-scoped dispatchers and receipt-bound rollback;
  arbitrary shell execution is outside this protocol.
- Schema and protocol changes require additive migration, regression tests, and
  an independently reviewed evidence receipt.
