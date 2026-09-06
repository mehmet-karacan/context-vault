# Context Vault A13 independent verification

Candidate SHA: `91b9bc6d9ffa89351dd557c4e6c36cdc5156b245`

Decision: **NO-GO for remote release; all executed local gates PASS.**

The candidate was independently checked from a clean detached checkout and fresh
runtime resources. Backend static checks, blank and restored migrations, unit and
integration suites, frontend build/browser accessibility, offline RAG E2E,
Context Vault protocol, full backup/destroy/restore, public-tree safety and the
container supply chain passed. No existing user database, object bucket, model
cache or project file was deleted or rebuilt.

The release decision remains fail-closed because the branch has no exact-SHA
GitHub Actions receipts, `main` has no ruleset/protection receipt, the image has no
remote OIDC signature, the current runner bundle has no newly issued human
benchmark approval/seal, and the public repository license choice is unresolved.
No push, merge, tag or release was performed.

The historical captured local real benchmark remains valid only for its recorded
older source and runner hashes. It is not projected as proof for this candidate.
The exact current local BGE-M3 and Qwen snapshots were nevertheless loaded offline
and executed successfully on Apple MPS; those runtime checks are provenance and
capability evidence, not a replacement for the guarded real-benchmark approval.
