import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now


class GlobalRole(StrEnum):
    ADMIN = "admin"
    USER = "user"
    AUDITOR = "auditor"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    username: str
    password_hash: str | None = None
    display_name: str = ""
    avatar_url: str = ""
    global_role: GlobalRole = GlobalRole.USER
    status: UserStatus = UserStatus.ACTIVE
    token_version: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_login_at: datetime | None = None

    @property
    def is_admin(self) -> bool:
        return self.global_role == GlobalRole.ADMIN

    @property
    def is_auditor(self) -> bool:
        return self.global_role == GlobalRole.AUDITOR

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE
