import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.models.artifact import Artifact
from .base import Base


class DeliveryArtifactModel(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_artifacts_id"),
        # Unguessable share links must be unique; only shared artifacts carry a token.
        Index(
            "uq_artifacts_share_token",
            "share_token",
            unique=True,
            postgresql_where=text("share_token IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="doc")
    title: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    storage_ref: Mapped[str] = mapped_column(String(1024), nullable=False, server_default="")
    version_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    share_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    share_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP(0)")
    )

    @classmethod
    def from_domain(cls, artifact: Artifact) -> "DeliveryArtifactModel":
        return cls(**artifact.model_dump(mode="python"))

    def to_domain(self) -> Artifact:
        return Artifact.model_validate(
            {
                "id": self.id,
                "session_id": self.session_id,
                "kind": self.kind,
                "title": self.title,
                "storage_ref": self.storage_ref,
                "version_refs": self.version_refs or [],
                "status": self.status,
                "share_token": self.share_token,
                "share_expires_at": self.share_expires_at,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )

    def update_from_domain(self, artifact: Artifact) -> None:
        for field, value in artifact.model_dump(
            mode="python",
            exclude={"created_at"},
        ).items():
            setattr(self, field, value)
