import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now


class ServiceApiKey(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner_user_id: str
    name: str
    key_hash: str
    prefix: str
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None
