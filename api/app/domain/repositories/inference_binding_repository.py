from abc import ABC, abstractmethod

from app.domain.models.inference import InferenceBinding, InferencePurpose
from app.domain.models.scope import OwnerScope


class InferenceBindingRepository(ABC):
    @abstractmethod
    async def get_exact(
        self,
        purpose: InferencePurpose,
        scope: OwnerScope | None,
    ) -> InferenceBinding | None: ...

    @abstractmethod
    async def get_effective_binding(
        self,
        purpose: InferencePurpose,
        scope: OwnerScope | None,
    ) -> InferenceBinding | None: ...

    @abstractmethod
    async def get_all_effective(
        self,
        scope: OwnerScope | None,
    ) -> list[InferenceBinding]: ...

    @abstractmethod
    async def save(
        self,
        binding: InferenceBinding,
        scope: OwnerScope | None,
    ) -> None: ...

    @abstractmethod
    async def count_for_model(self, model_id: str) -> int:
        """Bindings (any scope) still pointing at this model."""
        ...

    @abstractmethod
    async def delete_scoped_binding(
        self,
        purpose: InferencePurpose,
        scope: OwnerScope | None,
    ) -> None: ...
