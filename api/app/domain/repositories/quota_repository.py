from abc import ABC, abstractmethod

from app.domain.models.user_quota import UserQuota


class QuotaRepository(ABC):
    @abstractmethod
    async def get_for_user(self, user_id: str) -> UserQuota | None: ...

    @abstractmethod
    async def save(self, quota: UserQuota) -> None: ...
