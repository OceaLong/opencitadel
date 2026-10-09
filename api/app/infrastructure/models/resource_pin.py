"""Current ORM metadata mirrors the fixed F06 forward DDL."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from .base import Base


def _scope(name):
    return [
        sa.Column("owner_user_id", sa.String(255)),
        sa.Column("team_id", sa.String(255)),
        sa.Column(
            "scope_key",
            sa.String(261),
            sa.Computed(
                "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
                persisted=True,
            ),
        ),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint("schema_version>0", name=name + "_schema_version_check"),
        sa.CheckConstraint(
            "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
            name=name + "_check",
        ),
    ]


class ResourcePinORM(Base):
    __table__ = sa.Table(
        "resource_pins",
        Base.metadata,
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("owner_kind", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("resource_kind", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=False),
        sa.Column("resource_version", sa.String(255), nullable=False),
        sa.Column("available", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("unavailable_at", sa.DateTime(timezone=True)),
        sa.Column("unavailable_reason", sa.String(128)),
        *_scope("resource_pins"),
        *[
            sa.CheckConstraint(f"length({c})>0", name="resource_pins_" + c + "_check")
            for c in ("owner_kind", "owner_id", "resource_id", "resource_version")
        ],
        sa.CheckConstraint(
            "resource_kind IN ('knowledge_base','artifact','file','execution_content')",
            name="resource_pins_resource_kind_check",
        ),
        sa.CheckConstraint(
            "(available AND unavailable_at IS NULL AND unavailable_reason IS NULL) OR (NOT available AND unavailable_at IS NOT NULL AND unavailable_reason IS NOT NULL)",
            name="resource_pins_check1",
        ),
        sa.UniqueConstraint(
            "scope_key",
            "owner_kind",
            "owner_id",
            "resource_kind",
            "resource_id",
            "resource_version",
        ),
        sa.Index(
            "ix_resource_pins_resource",
            "resource_kind",
            "resource_id",
            "resource_version",
            postgresql_where=sa.text("available"),
        ),
        sa.Index("ix_resource_pins_owner", "scope_key", "owner_kind", "owner_id"),
    )


class ExecutionPublicContentORM(Base):
    __table__ = sa.Table(
        "execution_public_content",
        Base.metadata,
        sa.Column("content_id", sa.Uuid, primary_key=True),
        sa.Column("command_id", sa.Uuid, nullable=False),
        sa.Column("run_id", sa.Uuid, nullable=False),
        sa.Column("activity_id", sa.Uuid, nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("claim_generation", sa.Integer, nullable=False),
        sa.Column("phase", sa.String(8), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger, nullable=False),
        sa.Column("redacted", sa.Boolean, nullable=False),
        sa.Column("citation_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        *_scope("execution_public_content"),
        *[
            sa.CheckConstraint(condition, name="execution_public_content_" + name + "_check")
            for name, condition in [
                ("generation", "generation>=0"),
                ("claim_generation", "claim_generation>0"),
                ("phase", "phase IN ('input','output')"),
                ("byte_length", "byte_length>=0"),
            ]
        ],
        sa.UniqueConstraint("scope_key", "command_id", "phase"),
    )


class ExecutionContentBindingORM(Base):
    __table__ = sa.Table(
        "execution_content_bindings",
        Base.metadata,
        sa.Column("event_id", sa.Uuid, primary_key=True),
        sa.Column("phase", sa.String(8), primary_key=True),
        sa.Column(
            "content_id",
            sa.Uuid,
            sa.ForeignKey("execution_public_content.content_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Uuid, nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("formal_position", sa.BigInteger, nullable=False),
        *_scope("execution_content_bindings"),
        sa.CheckConstraint(
            "phase IN ('input','output')", name="execution_content_bindings_phase_check"
        ),
        sa.CheckConstraint(
            "formal_position>0", name="execution_content_bindings_formal_position_check"
        ),
    )


class ArtifactRetiredObjectORM(Base):
    __table__ = sa.Table(
        "artifact_retired_objects",
        Base.metadata,
        sa.Column("retirement_id", sa.Uuid, primary_key=True),
        sa.Column("artifact_id", sa.String(255), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("cleaned_at", sa.DateTime(timezone=True)),
        *_scope("artifact_retired_objects"),
        sa.UniqueConstraint("scope_key", "artifact_id", "storage_key"),
    )
