import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now

NotificationType = Literal[
    "job_complete",
    "job_failed",
    "approval_waiting",
    "artifact_final",
]


class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    type: NotificationType
    session_id: str | None = None
    artifact_id: str | None = None
    job_id: str | None = None
    message: str = ""
    i18n_key: str | None = None
    i18n_params: dict[str, str] | None = None
    read: bool = False
    created_at: datetime = Field(default_factory=utc_now)
