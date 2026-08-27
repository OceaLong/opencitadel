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
