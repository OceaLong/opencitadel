import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now


class RefreshToken(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str = ""
    ip_address: str = ""
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None
