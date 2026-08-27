from typing import Protocol

from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord


class MCPServerRepository(Protocol):
    async def list_all(self, scope: object | None = None) -> list[MCPServerRecord]: ...

    async def get_by_id(
        self, server_id: str, scope: object | None = None
    ) -> MCPServerRecord | None: ...

    async def get_by_name(
        self, name: str, scope: object | None = None
    ) -> MCPServerRecord | None: ...

    async def exists_global_name(self, name: str) -> bool: ...

    async def save(
        self,
        record: MCPServerRecord,
        encrypted_url: str | None,
        url_encryption: str,
        encrypted_headers: dict | None,
        headers_encryption: str,
        encrypted_env: dict | None,
        env_encryption: str,
    ) -> None: ...

    async def delete_by_id(self, server_id: str) -> None: ...


class A2AServerRepository(Protocol):
    async def list_all(self, scope: object | None = None) -> list[A2AServerRecord]: ...

    async def get_by_id(
        self, server_id: str, scope: object | None = None
    ) -> A2AServerRecord | None: ...

    async def save(self, record: A2AServerRecord) -> None: ...

    async def delete_by_id(self, server_id: str) -> None: ...
