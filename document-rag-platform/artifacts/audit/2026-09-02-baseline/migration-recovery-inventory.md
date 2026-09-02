# CV3 Missing Migration Recovery Inventory

Target revisions: `b2f1c0a10004`, `b2f1c0a10005`.

## Read-only sources checked

- Reachable Git history and all fetched refs.
- Local clone reflog, worktrees, stashes, unreachable Git objects, and path/log
  searches.
- GitHub branches, pull requests, workflow artifacts, and reachable workflow
  commit metadata.
- Other matching local clones under the approved project area.
- Provided Downloads/task material and the previous Context Vault directory.
- Available editor local history for exact revision identifiers.
- Local Docker contexts, matching containers, volumes, networks, and images.
- Backup filename inventory in the approved project/document/download roots.
- History-rewrite Git bundles and repository archive candidates across the local
  user storage roots.
- GitHub Actions heads and PushEvent ref transitions for reachable deleted-branch
  or pre-rewrite commit candidates.

## Result

No exact migration file, blob, bundle, artifact, old clone, container, volume, or
backup for the two target revisions was found. The compose-declared PostgreSQL
and MinIO named volumes no longer exist in the available Docker daemon; the one
anonymous volume is empty. Text matches were limited to the active task and
historical audit claims.

## Recovery decision

- `RECOVER_EXACT_MIGRATIONS`: rejected with current evidence.
- `V3_NEW_DB_LINEAGE_RESET_AND_CUTOVER`: selected and owner-authorized for a
  new empty isolated lineage.

Because the legacy source does not exist in the available environment, no claim
is made that legacy data was restored or migrated. The owner accepted that loss
and authorized a new lineage. A checksum-verified synthetic PostgreSQL and MinIO
restore drill was completed before the V3 baseline was admitted. If exact files
or a legacy backup are later supplied, their revision chain, hashes, provenance,
and schema effect must be verified before any import is attempted.

Forbidden actions remain forbidden: guessed/no-op revisions, live `stamp`,
manual Alembic version edits, blind downgrade, source reset, or source deletion.

## First future application mutation plan

The first application-code change after admission is:

`fix(migrations): decouple migration config from application secrets`

Exact initial scope:

- `document-rag-platform/services/backend/alembic/env.py`
- a new DB-only migration settings module under
  `document-rag-platform/services/backend/src/`
- focused tests proving migration configuration does not load LLM, Redis, MinIO,
  or provider secrets

Required tests:

- missing LLM/provider keys do not prevent `alembic heads`;
- blank isolated PostgreSQL can execute the known source chain;
- multiple heads fail;
- application startup performs a read-only exact-head check and no DDL.

Rollback: revert only that local commit. It must contain no migration revision,
schema/data write, formatter sweep, CI trigger change, or runtime cutover.
