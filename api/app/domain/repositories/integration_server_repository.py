#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional, Protocol

from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord


class MCPServerRepository(Protocol):
    async def list_all(self, scope: Optional[object] = None) -> List[MCPServerRecord]:
        ...

    async def get_by_id(self, server_id: str, scope: Optional[object] = None) -> Optional[MCPServerRecord]:
        ...

    async def get_by_name(self, name: str, scope: Optional[object] = None) -> Optional[MCPServerRecord]:
        ...

    async def exists_global_name(self, name: str) -> bool:
        ...

    async def save(
        self,
        record: MCPServerRecord,
        encrypted_url: Optional[str],
        url_encryption: str,
        encrypted_headers: Optional[dict],
        headers_encryption: str,
        encrypted_env: Optional[dict],
        env_encryption: str,
    ) -> None:
        ...

    async def delete_by_id(self, server_id: str) -> None:
        ...


class A2AServerRepository(Protocol):
    async def list_all(self, scope: Optional[object] = None) -> List[A2AServerRecord]:
        ...

    async def get_by_id(self, server_id: str, scope: Optional[object] = None) -> Optional[A2AServerRecord]:
        ...

    async def save(self, record: A2AServerRecord) -> None:
        ...

    async def delete_by_id(self, server_id: str) -> None:
        ...
