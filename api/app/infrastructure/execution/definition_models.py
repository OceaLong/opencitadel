"""Forward-owned definition copies and operational routing; no Run lifecycle."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

definitions = sa.Table(
    "execution_definitions",
    metadata,
    sa.Column("family", sa.String(32), primary_key=True),
    sa.Column("definition_version", sa.Integer, primary_key=True),
    sa.Column("manifest_digest", sa.String(64), nullable=False, unique=True),
    sa.Column("body", JSONB, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("retired_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("definition_version>0", name="ck_definition_version"),
)
heads = sa.Table(
    "execution_definition_heads",
    metadata,
    sa.Column("family", sa.String(32), primary_key=True),
    sa.Column(
        "manifest_digest",
        sa.String(64),
        sa.ForeignKey(definitions.c.manifest_digest, ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)
workers = sa.Table(
    "execution_worker_capabilities",
    metadata,
    sa.Column("worker_id", sa.String(255), primary_key=True),
    sa.Column("boot_id", sa.String(255), primary_key=True),
    sa.Column("body", JSONB, nullable=False),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_execution_worker_capability_expiry", "expires_at"),
)
changes = sa.Table(
    "execution_definition_changes",
    metadata,
    sa.Column("request_id", sa.UUID, primary_key=True),
    sa.Column("actor_user_id", sa.String(255), nullable=False),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("family", sa.String(32), nullable=False),
    sa.Column("expected_digest", sa.String(64), nullable=False),
    sa.Column("manifest_digest", sa.String(64), nullable=False),
    sa.Column("reason", sa.String(2000), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)
admissions = sa.Table(
    "execution_definition_admissions",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("command_id", sa.UUID, primary_key=True),
    sa.Column("run_id", sa.UUID, nullable=False),
    sa.Column(
        "manifest_digest",
        sa.String(64),
        sa.ForeignKey(definitions.c.manifest_digest, ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)
