import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now

NotificationType = Literal[
    "job_started",
    "job_complete",
    "job_failed",
    "approval_waiting",
    "approval_expired",
    "clarification_waiting",
    "clarification_expired",
    "artifact_final",
    "patrol_complete",
]


class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    type: NotificationType
    session_id: str | None = None
    # 关联的审批/澄清 id：审批被决定、过期或随 Run 取消时，据此把等待通知
    # 自动标记为已读（"处理了还是有提示"的根治点）。
    approval_id: str | None = None
    artifact_id: str | None = None
    job_id: str | None = None
    message: str = ""
    i18n_key: str | None = None
    i18n_params: dict[str, str] | None = None
    read: bool = False
    created_at: datetime = Field(default_factory=utc_now)
