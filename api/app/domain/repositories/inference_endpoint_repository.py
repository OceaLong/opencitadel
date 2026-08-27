from abc import ABC, abstractmethod

from app.domain.models.inference import InferenceEndpoint
from app.domain.models.scope import OwnerScope


class InferenceEndpointRepository(ABC):
    @abstractmethod
    async def get_all(self, scope: OwnerScope | None = None) -> list[InferenceEndpoint]: ...

    @abstractmethod
    async def list_hosts(self, scope: OwnerScope | None = None) -> list[str]: ...

    @abstractmethod
    async def get_by_id(
        self,
        endpoint_id: str,
        scope: OwnerScope | None = None,
    ) -> InferenceEndpoint | None: ...

    @abstractmethod
    async def save(self, endpoint: InferenceEndpoint, encrypted_credential: str) -> None: ...

    @abstractmethod
    async def delete_by_id(self, endpoint_id: str) -> None: ...

    @abstractmethod
    async def count_models(self, endpoint_id: str) -> int: ...
