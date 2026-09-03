from abc import ABC, abstractmethod

from app.domain.models.service_api_key import ServiceApiKey


class ServiceApiKeyRepository(ABC):
    @abstractmethod
    async def get_by_hash(self, key_hash: str) -> ServiceApiKey | None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[ServiceApiKey]: ...

    @abstractmethod
    async def save(self, key: ServiceApiKey) -> None: ...

    @abstractmethod
    async def rotate(
        self, key_id: str, user_id: str, *, key_hash: str, prefix: str
    ) -> ServiceApiKey | None: ...

    @abstractmethod
    async def revoke(self, key_id: str, user_id: str) -> None: ...
