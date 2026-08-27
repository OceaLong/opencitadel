from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.inference import InferenceBinding, InferencePurpose

from .base import Base


class InferenceBindingORM(Base):
    __tablename__ = "inference_bindings"

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    model_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("inference_models.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_key",
            "purpose",
            name="uq_inference_bindings_scope_purpose",
        ),
        CheckConstraint(
            """
            (scope_type = 'global' AND scope_key = 'global'
                AND owner_user_id IS NULL AND team_id IS NULL)
            OR (scope_type = 'user' AND scope_key = owner_user_id
                AND owner_user_id IS NOT NULL AND team_id IS NULL)
            OR (scope_type = 'team' AND scope_key = team_id
                AND owner_user_id IS NULL AND team_id IS NOT NULL)
            """,
            name="ck_inference_bindings_scope_owner",
        ),
    )

    def to_domain(self) -> InferenceBinding:
        return InferenceBinding(
            id=self.id,
            purpose=InferencePurpose(self.purpose),
            model_id=self.model_id,
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
