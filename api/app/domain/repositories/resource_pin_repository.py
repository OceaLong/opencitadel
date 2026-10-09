from typing import Protocol

from app.domain.models.resource_pin import PinValidation, ResourceIdentity
from app.domain.models.scope import OwnerScope


class ResourcePinRepository(Protocol):
    async def acquire(
        self, scope: OwnerScope, owner_kind: str, owner_id: str, resources: list[ResourceIdentity]
    ) -> None: ...
    async def release(
        self, scope: OwnerScope, owner_kind: str, owner_id: str, resources: list[ResourceIdentity]
    ) -> None: ...
    async def validate(
        self, scope: OwnerScope, owner_kind: str, owner_id: str, resources: list[ResourceIdentity]
    ) -> list[PinValidation]: ...
