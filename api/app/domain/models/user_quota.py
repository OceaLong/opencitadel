from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now


class UserQuota(BaseModel):
    user_id: str
    monthly_token_limit: int | None = Field(default=None, ge=0)
    daily_session_limit: int | None = Field(default=None, ge=0)
    max_concurrent_tasks: int | None = Field(default=None, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamQuota(BaseModel):
    """团队/租户维度配额，字段镜像 :class:`UserQuota`，主键为 ``team_id``。

    多租户平台上，会话/Token/并发/存储既受个人配额约束，也受所属团队配额
    约束；准入处对两者独立校验（任一超限即拒绝），等效于取更严者。
    """

    team_id: str
    monthly_token_limit: int | None = Field(default=None, ge=0)
    daily_session_limit: int | None = Field(default=None, ge=0)
    max_concurrent_tasks: int | None = Field(default=None, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
