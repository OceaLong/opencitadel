from abc import ABC, abstractmethod

from app.domain.models.inference import InferenceModel
from app.domain.models.scope import OwnerScope


class InferenceModelRepository(ABC):
    @abstractmethod
    async def get_all(self, scope: OwnerScope | None = None) -> list[InferenceModel]: ...

    @abstractmethod
    async def get_all_global(self) -> list[InferenceModel]: ...

    @abstractmethod
    async def get_by_id(
        self,
        model_id: str,
        scope: OwnerScope | None = None,
    ) -> InferenceModel | None: ...

    @abstractmethod
    async def get_by_endpoint_id(
        self,
        endpoint_id: str,
        scope: OwnerScope | None = None,
    ) -> list[InferenceModel]: ...

    @abstractmethod
    async def save(self, model: InferenceModel) -> None: ...

    @abstractmethod
    async def delete_by_id(self, model_id: str) -> None: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def count_global(self) -> int: ...

    @abstractmethod
    async def count_by_endpoint_id(self, endpoint_id: str) -> int: ...
