from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    type: str
    session_id: str | None = None
    artifact_id: str | None = None
    job_id: str | None = None
    message: str
    i18n_key: str | None = None
    i18n_params: dict | None = None
    read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    unread_count: int


class NotificationDeliveryResponse(BaseModel):
    id: str
    channel_type: str
    message: str
    status: str
    attempts: int
    last_error: str | None = None
    next_attempt_at: datetime
    created_at: datetime
    sent_at: datetime | None = None


class NotificationDeliveryListResponse(BaseModel):
    deliveries: list[NotificationDeliveryResponse]
