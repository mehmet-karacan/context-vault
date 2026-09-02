# Offline E2E tier

This tier runs on fresh PostgreSQL/pgvector, Redis and MinIO and exercises the
production `IngestionOrchestrator`, durable worker path, scoped retrievers,
`ContextBundle`, structured answer validation and citation persistence. Its
deterministic providers never derive candidates or answers from golden labels.
