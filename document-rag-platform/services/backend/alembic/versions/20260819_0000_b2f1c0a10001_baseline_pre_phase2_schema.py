"""baseline: pre-Phase-2 schema marker (projects, documents, chunks)

Revision ID: b2f1c0a10001
Revises:
Create Date: 2026-08-19

This revision runs NO DDL. It exists only as a fixed point in the migration
graph that represents the schema as it existed before Aşama 2 (Context Vault
dönüşüm planı, AKTIF_GOREV.md Bölüm 3/8): the three tables ``projects``,
``documents`` and ``chunks`` exactly as defined by
``services/backend/src/models.py`` prior to this migration set — including
the existing ``chunks.embedding Vector(1024)`` column and its HNSW index,
neither of which this migration touches.

Usage (see MIGRATION_RUNBOOK.md for the full, exact sequence — do not run
any of this from here, and do not skip straight to ``alembic upgrade head``
without stamping first):

    A database that was created by the *old*, pre-Alembic ``init_db()``
    path (``Base.metadata.create_all`` in src/db.py) already has this exact
    schema, but Alembic has no record of it. Stamp it to this revision so
    Alembic considers it "at baseline" without trying to re-create the
    already-existing tables:

        alembic stamp b2f1c0a10001

    Only after that should ``alembic upgrade head`` be run, which then
    applies the additive schema migration and the data backfill on top.

    IMPORTANT: once the later revisions in this directory exist (which they
    do, as of this commit), `alembic stamp head` would stamp the database as
    already having those changes applied -- which is false and will make
    `alembic upgrade head` silently skip the real migrations. Always stamp
    the explicit revision id above, never "head", for this baseline step.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "b2f1c0a10001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty -- marker revision only, see module docstring.
    pass


def downgrade() -> None:
    # Intentionally empty -- marker revision only, see module docstring.
    pass
