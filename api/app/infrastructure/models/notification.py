import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.models.notification import Notification
from ...domain.utils.notification_message import decode_notification_message
from .base import Base


class NotificationModel(Base):
    __tablename__ = "notifications"
    __table_args__ = (PrimaryKeyConstraint("id", name="pk_notifications_id"),)

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    artifact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> Notification:
        message, i18n_key, i18n_params = decode_notification_message(self.message)
        return Notification.model_validate(
            {
                "id": self.id,
                "user_id": self.user_id,
                "type": self.type,
                "session_id": self.session_id,
                "approval_id": self.approval_id,
                "artifact_id": self.artifact_id,
                "job_id": self.job_id,
                "message": message,
                "i18n_key": i18n_key,
                "i18n_params": i18n_params,
                "read": self.read,
                "created_at": self.created_at,
            }
        )


class NotificationDeliveryModel(Base):
    """Durable, independently retriable outbound channel delivery."""

    __tablename__ = "notification_deliveries"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    channel: Mapped[dict] = mapped_column(JSON, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_domain(self):
        from app.domain.models.notification import NotificationDelivery

        return NotificationDelivery.model_validate(
            {column.name: getattr(self, column.name) for column in self.__table__.columns}
        )
