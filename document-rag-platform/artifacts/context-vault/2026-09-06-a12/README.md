# A12 Context Vault continuity evidence

This public-safe bundle records the local and independent verification of the
PostgreSQL Work Graph, bounded context compiler, reviewed knowledge lifecycle,
registries, CLI adapters, and handoff protocol.

The verification used disposable PostgreSQL 16/pgvector containers. No
long-lived user database, MinIO object, embedding/index record, or project file
outside the repository was mutated. OpenCode was not installed or executed;
OpenCode, Claude, and Codex behavior was verified through the provider-neutral
adapter conformance suite. Local registry behavior uses the BGE-compatible
embedding profile requested for the MacBook runtime.

The verified A12 implementation commit is
`8ac1c4f3f37310e1598ba623717529dba98676e4`. This follow-up binding changes
only documentation/evidence files; A13 clean-environment verification uses the
resulting candidate commit.
