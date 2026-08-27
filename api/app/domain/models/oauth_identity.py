import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now


class OAuthIdentity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    provider: str
    provider_user_id: str
    email: str = ""
    email_verified: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
