"""PostgreSQL authority for immutable Runtime Policy revisions and head."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models.base import Base


class ExecutionPolicyRevisionORM(Base):
    __tablename__ = "execution_policy_revisions"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="ck_execution_policy_schema_version"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    sequence: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        unique=True,
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    digest: Mapped[str] = mapped_column(String(71), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    restored_from_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("execution_policy_revisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )


class OperationsPolicyRevisionORM(Base):
    __tablename__ = "operations_policy_revisions"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="ck_operations_policy_schema_version"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    sequence: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        unique=True,
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    digest: Mapped[str] = mapped_column(String(71), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    restored_from_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations_policy_revisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )


class RuntimePolicyHeadORM(Base):
    __tablename__ = "runtime_policy_heads"
    __table_args__ = (
        CheckConstraint("id = 'global'", name="ck_runtime_policy_head_global"),
        CheckConstraint("version > 0", name="ck_runtime_policy_head_version"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    execution_revision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("execution_policy_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operations_revision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations_policy_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )


__all__ = [
    "ExecutionPolicyRevisionORM",
    "OperationsPolicyRevisionORM",
    "RuntimePolicyHeadORM",
]
