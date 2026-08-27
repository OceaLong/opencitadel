from typing import Protocol

from app.domain.models.resource_bindings import (
    ResourceKind,
    SessionResourceBinding,
)


class SessionResourceBindingRepository(Protocol):
    async def add_binding(
        self,
        binding: SessionResourceBinding,
    ) -> SessionResourceBinding: ...

    async def get_current_binding(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        *,
        for_update: bool = False,
    ) -> SessionResourceBinding | None: ...

    async def list_current_bindings(
        self,
        session_id: str,
    ) -> list[SessionResourceBinding]: ...

    async def list_bindings(
        self,
        session_id: str,
        resource_kind: ResourceKind | None = None,
    ) -> list[SessionResourceBinding]: ...

    async def replace_current_binding(
        self,
        current: SessionResourceBinding,
        replacement: SessionResourceBinding,
    ) -> SessionResourceBinding: ...
