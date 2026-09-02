"""Add fail-closed principal/workspace/API-key scope.

Revision ID: cv3_00000002
Revises: cv3_00000001
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "cv3_00000002"
down_revision: str | None = "cv3_00000001"
branch_labels = None
depends_on = None

LEGACY_PRINCIPAL_ID = "11111111-1111-4111-8111-111111111111"
LEGACY_WORKSPACE_ID = "22222222-2222-4222-8222-222222222222"


def upgrade() -> None:
    op.create_table(
        "principals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'member', 'reader')", name="ck_workspace_memberships_role"
        ),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_principal_id", "api_keys", ["principal_id"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])

    # Existing projects are preserved under an explicitly named, inactive
    # legacy owner. No credential or usable default API key is created.
    op.execute(
        sa.text(
            "INSERT INTO principals (id, subject, display_name, is_active) "
            "VALUES (CAST(:id AS uuid), 'migration:legacy-owner', "
            "'Legacy data owner (inactive)', false)"
        ).bindparams(id=LEGACY_PRINCIPAL_ID)
    )
    op.execute(
        sa.text(
            "INSERT INTO workspaces (id, name) "
            "VALUES (CAST(:id AS uuid), 'Legacy migrated workspace')"
        ).bindparams(id=LEGACY_WORKSPACE_ID)
    )
    op.execute(
        sa.text(
            "INSERT INTO workspace_memberships (workspace_id, principal_id, role) "
            "VALUES (CAST(:workspace_id AS uuid), CAST(:principal_id AS uuid), 'admin')"
        ).bindparams(
            workspace_id=LEGACY_WORKSPACE_ID, principal_id=LEGACY_PRINCIPAL_ID
        )
    )

    op.add_column(
        "projects", sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.execute(
        sa.text("UPDATE projects SET workspace_id = CAST(:id AS uuid)").bindparams(
            id=LEGACY_WORKSPACE_ID
        )
    )
    op.alter_column("projects", "workspace_id", nullable=False)
    op.create_foreign_key(
        "fk_projects_workspace_id",
        "projects",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"])
    op.drop_constraint("projects_name_key", "projects", type_="unique")
    op.create_unique_constraint(
        "uq_projects_workspace_name", "projects", ["workspace_id", "name"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_projects_workspace_name", "projects", type_="unique")
    op.create_unique_constraint("projects_name_key", "projects", ["name"])
    op.drop_index("ix_projects_workspace_id", table_name="projects")
    op.drop_constraint("fk_projects_workspace_id", "projects", type_="foreignkey")
    op.drop_column("projects", "workspace_id")
    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_principal_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")
    op.drop_table("principals")
