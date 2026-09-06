# Context Vault exact-candidate verification

Candidate SHA: `8ce08125d16f245009a93e6933575684a55ad62a`

Decision: **GO for the owner-approved local MacBook deployment scope.**

The aggregate strict verifier accepted all 15 required exact-SHA evidence
components with zero findings. Ten push/PR workflows and all 15 GitHub check
runs passed; the branch ruleset remained active and the prior direct-push probe
was rejected with GH013.

Backend CI collected 1,513 unit and 34 integration tests with zero failures and
84% combined coverage. Frontend lint, typecheck, generated-client drift, 26 unit
tests, production build/image and 20 browser tests passed. The offline RAG suite
passed 55 tests with zero scope leakage and invalid citations. The owner-approved
local BGE-M3/Qwen exact benchmark passed its sealed regression baseline and an
independent checker proved that golden results were not transferred.

A new fresh-target drill restored a custom PostgreSQL dump and one encrypted,
versioned MinIO object into distinct empty targets. Migration, auth, retrieval
and citation smokes passed; reconciliation was missing/orphan `0/0`, RPO was
`0.508s`, and RTO was `0.531s`.

The active local deployment SLO policy is bound to an owner-approved,
content-free baseline. Fifty live HTTP probes succeeded, all eleven SLO checks
passed, and GitHub issue #2 proves external alert assignment and owner ACK. The
repository now states explicit proprietary/no-license terms. Only the P2 future
admission items in `known-risks.md` remain.

No existing user database, MinIO artifact, model cache or project file was
deleted or reset. Private benchmark and backup artifacts remain outside Git;
this package carries only public-safe summaries and cryptographic hashes.
