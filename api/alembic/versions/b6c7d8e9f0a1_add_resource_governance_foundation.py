"""add resource governance foundation

Revision ID: b6c7d8e9f0a1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_approval_batches",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tool_approval_batches_pending",
        "tool_approval_batches",
        ["session_id", "status", "created_at"],
    )

    op.create_table(
        "tool_approval_calls",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("batch_id", sa.String(length=255), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column(
            "normalized_args",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("effect", sa.String(length=64), nullable=False),
        sa.Column("idempotency", sa.String(length=64), nullable=False),
        sa.Column("approval", sa.String(length=32), nullable=False),
        sa.Column("concurrency_group", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["tool_approval_batches.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tool_call_id", name="uq_tool_approval_calls_tool_call_id"
        ),
        sa.UniqueConstraint(
            "batch_id",
            "ordinal",
            name="uq_tool_approval_calls_batch_ordinal",
        ),
    )

    op.create_table(
        "resource_builds",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("resource_kind", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("version_id", sa.String(length=255), nullable=False),
        sa.Column("parent_version_id", sa.String(length=255), nullable=True),
        sa.Column("command_key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column(
            "progress", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "degraded_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_event_seq", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_resource_builds_active",
        "resource_builds",
        ["resource_kind", "resource_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )

    op.create_table(
        "session_resource_bindings",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("resource_kind", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("version_id", sa.String(length=255), nullable=False),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("supersedes_binding_id", sa.String(length=255), nullable=True),
        sa.Column("bound_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_binding_id"],
            ["session_resource_bindings.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_session_resource_bindings_current",
        "session_resource_bindings",
        ["session_id", "resource_kind"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "resource_build_events",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("build_id", sa.String(length=255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "progress", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["build_id"], ["resource_builds.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "build_id", "seq", name="uq_resource_build_events_build_seq"
        ),
    )


def downgrade() -> None:
    op.drop_table("resource_build_events")
    op.drop_index(
        "uq_session_resource_bindings_current",
        table_name="session_resource_bindings",
    )
    op.drop_table("session_resource_bindings")
    op.drop_index("uq_resource_builds_active", table_name="resource_builds")
    op.drop_table("resource_builds")
    op.drop_table("tool_approval_calls")
    op.drop_index(
        "ix_tool_approval_batches_pending",
        table_name="tool_approval_batches",
    )
    op.drop_table("tool_approval_batches")
