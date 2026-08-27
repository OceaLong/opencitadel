from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.resource_bindings import (
    ResourceKind,
    SessionResourceBinding,
)

from .base import Base


class SessionResourceBindingORM(Base):
    __tablename__ = "session_resource_bindings"
    __table_args__ = (
        Index(
            "uq_session_resource_bindings_current",
            "session_id",
            "resource_kind",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
        Index(
            "ix_session_resource_bindings_resource_version",
            "resource_kind",
            "resource_id",
            "version_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    supersedes_binding_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("session_resource_bindings.id", ondelete="SET NULL"),
        nullable=True,
    )
    bound_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def to_domain(self) -> SessionResourceBinding:
        return SessionResourceBinding(
            id=self.id,
            session_id=self.session_id,
            resource_kind=ResourceKind(self.resource_kind),
            resource_id=self.resource_id,
            version_id=self.version_id,
            is_current=self.is_current,
            supersedes_binding_id=self.supersedes_binding_id,
            bound_by=self.bound_by,
            created_at=_as_utc(self.created_at),
        )

    @classmethod
    def from_domain(
        cls,
        binding: SessionResourceBinding,
    ) -> "SessionResourceBindingORM":
        return cls(
            id=binding.id,
            session_id=binding.session_id,
            resource_kind=binding.resource_kind.value,
            resource_id=binding.resource_id,
            version_id=binding.version_id,
            is_current=binding.is_current,
            supersedes_binding_id=binding.supersedes_binding_id,
            bound_by=binding.bound_by,
            created_at=binding.created_at,
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
