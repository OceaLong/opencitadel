"""Forward-only metadata: Run events own facts; these tables are recoverable indexes."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()


def scope_columns():
    return [
        sa.Column("scope_key", sa.String(261), nullable=False),
        sa.Column("owner_user_id", sa.String(255)),
        sa.Column("team_id", sa.String(255)),
    ]


operations = sa.Table(
    "execution_effect_operations",
    metadata,
    sa.Column("operation_id", sa.UUID(), primary_key=True),
    *scope_columns(),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("invocation_id", sa.UUID(), nullable=False),
    sa.Column("activity_id", sa.UUID(), nullable=False),
    sa.Column("provider_identity", sa.String(255), nullable=False),
    sa.Column("idempotency_key", sa.String(255), nullable=False),
    sa.Column("body", JSONB(), nullable=False),
    sa.Column("revision", sa.Integer(), nullable=False),
    sa.Column("projected_revision", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("last_event_position", sa.BigInteger(), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("scope_key", "invocation_id", name="uq_effect_invocation"),
    sa.UniqueConstraint(
        "scope_key", "provider_identity", "idempotency_key", name="uq_effect_provider_key"
    ),
    sa.Index("ix_effect_run", "scope_key", "run_id"),
)
receipts = sa.Table(
    "execution_effect_receipts",
    metadata,
    sa.Column("receipt_id", sa.UUID(), primary_key=True),
    *scope_columns(),
    sa.Column("operation_id", sa.UUID(), nullable=False),
    sa.Column("event_id", sa.UUID(), nullable=False, unique=True),
    sa.Column("event_position", sa.BigInteger(), nullable=False),
    sa.Column("body", JSONB(), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Index("ix_effect_receipt_operation", "scope_key", "operation_id"),
)
requests = sa.Table(
    "execution_effect_requests",
    metadata,
    sa.Column("request_id", sa.UUID(), primary_key=True),
    *scope_columns(),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("operation_id", sa.UUID(), nullable=False),
    sa.Column("kind", sa.String(30), nullable=False),
    sa.Column("idempotency_key", sa.String(255), nullable=False),
    sa.Column("fingerprint", sa.String(64), nullable=False),
    sa.Column("expected_revision", sa.Integer(), nullable=False),
    sa.Column("principal", JSONB()),
    sa.Column("scope_reason", sa.Text()),
    sa.Column("body", JSONB(), nullable=False),
    sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
    sa.Column("error_code", sa.String(128)),
    sa.Column("command_type", sa.String(100)),
    sa.Column("command_payload_digest", sa.String(64)),
    sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("claim_id", sa.UUID()),
    sa.Column("claim_until", sa.DateTime(timezone=True)),
    sa.Column("due_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint(
        "scope_key", "operation_id", "idempotency_key", name="uq_effect_request_key"
    ),
    sa.Index("ix_effect_request_due", "status", "due_at"),
)

for table in metadata.tables.values():
    table.append_constraint(
        sa.CheckConstraint(
            "(owner_user_id IS NOT NULL AND team_id IS NULL AND scope_key='user:'||owner_user_id) OR (owner_user_id IS NULL AND team_id IS NOT NULL AND scope_key='team:'||team_id)",
            name=f"ck_{table.name}_scope",
        )
    )
