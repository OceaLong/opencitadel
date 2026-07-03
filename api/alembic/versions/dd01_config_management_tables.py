"""config management: scoped app_configs, revisions, mcp/a2a servers

Revision ID: dd01configmgmt
Revises: cc02joberr
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dd01configmgmt"
down_revision: Union[str, None] = "cc02joberr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_configs",
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="global"),
    )
    op.add_column(
        "app_configs",
        sa.Column("owner_user_id", sa.String(length=255), nullable=True),
    )
    op.execute(sa.text("UPDATE app_configs SET id = 'global' WHERE id = 'default'"))
    op.execute(sa.text("UPDATE app_configs SET scope = 'global' WHERE id = 'global'"))
    op.create_index("ix_app_configs_scope_owner", "app_configs", ["scope", "owner_user_id"], unique=True)

    op.create_table(
        "app_config_revisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("config_id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("changed_by", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_app_config_revisions_id"),
    )
    op.create_index(
        "ix_app_config_revisions_config_id",
        "app_config_revisions",
        ["config_id", "created_at"],
    )

    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("transport", sa.String(length=64), nullable=False, server_default="streamable_http"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("headers_encryption", sa.String(length=32), nullable=False, server_default="legacy_plaintext"),
        sa.Column("env", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("env_encryption", sa.String(length=32), nullable=False, server_default="legacy_plaintext"),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("owner_user_id", sa.String(length=255), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("visibility", sa.String(length=32), nullable=False, server_default="global"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_servers_id"),
        sa.UniqueConstraint("name", "owner_user_id", "visibility", name="uq_mcp_servers_name_owner_visibility"),
    )

    op.create_table(
        "a2a_servers",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("owner_user_id", sa.String(length=255), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("visibility", sa.String(length=32), nullable=False, server_default="global"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_a2a_servers_id"),
    )


def downgrade() -> None:
    op.drop_table("a2a_servers")
    op.drop_table("mcp_servers")
    op.drop_index("ix_app_config_revisions_config_id", table_name="app_config_revisions")
    op.drop_table("app_config_revisions")
    op.drop_index("ix_app_configs_scope_owner", table_name="app_configs")
    op.drop_column("app_configs", "owner_user_id")
    op.drop_column("app_configs", "scope")
    op.execute(sa.text("UPDATE app_configs SET id = 'default' WHERE id = 'global'"))
