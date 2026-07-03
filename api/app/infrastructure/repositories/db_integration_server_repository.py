#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import or_, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.scope import OwnerScope
from app.domain.repositories.integration_server_repository import A2AServerRepository, MCPServerRepository
from app.infrastructure.models.integration_server import A2AServerORM, MCPServerORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.secret_dict_cipher import decrypt_secret_dict


class DBMCPServerRepository(MCPServerRepository):
    def __init__(self, db_session: AsyncSession, cipher: ApiKeyCipher) -> None:
        self.db_session = db_session
        self.cipher = cipher

    def _apply_scope(self, stmt, scope: Optional[OwnerScope]):
        """scope=None means global-only (unlike llm_model repo where None means no filter)."""
        if scope is None:
            return stmt.where(MCPServerORM.visibility == "global")
        owner_filter = MCPServerORM.owner_user_id == scope.user_id
        return stmt.where(or_(MCPServerORM.visibility == "global", owner_filter))

    def _to_domain(self, record: MCPServerORM) -> MCPServerRecord:
        headers = decrypt_secret_dict(record.headers, record.headers_encryption, self.cipher)
        env = decrypt_secret_dict(record.env, record.env_encryption, self.cipher)
        return record.to_domain(headers, env)

    async def list_all(self, scope: Optional[OwnerScope] = None) -> List[MCPServerRecord]:
        stmt = self._apply_scope(select(MCPServerORM), scope).order_by(MCPServerORM.created_at)
        result = await self.db_session.execute(stmt)
        return [self._to_domain(r) for r in result.scalars().all()]

    async def get_by_id(self, server_id: str, scope: Optional[OwnerScope] = None) -> Optional[MCPServerRecord]:
        stmt = self._apply_scope(select(MCPServerORM).where(MCPServerORM.id == server_id), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return self._to_domain(record) if record else None

    async def get_by_name(self, name: str, scope: Optional[OwnerScope] = None) -> Optional[MCPServerRecord]:
        stmt = self._apply_scope(select(MCPServerORM).where(MCPServerORM.name == name), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return self._to_domain(record) if record else None

    async def exists_global_name(self, name: str) -> bool:
        stmt = select(MCPServerORM.id).where(
            MCPServerORM.name == name,
            MCPServerORM.visibility == "global",
        )
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def save(
        self,
        record: MCPServerRecord,
        encrypted_headers: Optional[dict],
        headers_encryption: str,
        encrypted_env: Optional[dict],
        env_encryption: str,
    ) -> None:
        result = await self.db_session.execute(select(MCPServerORM).where(MCPServerORM.id == record.id))
        existing = result.scalar_one_or_none()
        record.updated_at = datetime.now()
        if existing:
            existing.name = record.name
            existing.transport = record.transport.value
            existing.enabled = record.enabled
            existing.description = record.description
            existing.command = record.command
            existing.args = record.args
            existing.url = record.url
            if encrypted_headers is not None:
                existing.headers = encrypted_headers
                existing.headers_encryption = headers_encryption
            if encrypted_env is not None:
                existing.env = encrypted_env
                existing.env_encryption = env_encryption
            existing.extra = record.extra
            existing.owner_user_id = record.owner_user_id
            existing.visibility = record.visibility.value if hasattr(record.visibility, "value") else record.visibility
            existing.updated_at = record.updated_at
        else:
            self.db_session.add(
                MCPServerORM(
                    id=record.id,
                    name=record.name,
                    transport=record.transport.value,
                    enabled=record.enabled,
                    description=record.description,
                    command=record.command,
                    args=record.args,
                    url=record.url,
                    headers=encrypted_headers,
                    headers_encryption=headers_encryption,
                    env=encrypted_env,
                    env_encryption=env_encryption,
                    extra=record.extra,
                    owner_user_id=record.owner_user_id,
                    visibility=record.visibility.value if hasattr(record.visibility, "value") else record.visibility,
                )
            )

    async def delete_by_id(self, server_id: str) -> None:
        await self.db_session.execute(delete(MCPServerORM).where(MCPServerORM.id == server_id))


class DBA2AServerRepository(A2AServerRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    def _apply_scope(self, stmt, scope: Optional[OwnerScope]):
        """scope=None means global-only (unlike llm_model repo where None means no filter)."""
        if scope is None:
            return stmt.where(A2AServerORM.visibility == "global")
        owner_filter = A2AServerORM.owner_user_id == scope.user_id
        return stmt.where(or_(A2AServerORM.visibility == "global", owner_filter))

    async def list_all(self, scope: Optional[OwnerScope] = None) -> List[A2AServerRecord]:
        stmt = self._apply_scope(select(A2AServerORM), scope).order_by(A2AServerORM.created_at)
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def get_by_id(self, server_id: str, scope: Optional[OwnerScope] = None) -> Optional[A2AServerRecord]:
        stmt = self._apply_scope(select(A2AServerORM).where(A2AServerORM.id == server_id), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save(self, record: A2AServerRecord) -> None:
        result = await self.db_session.execute(select(A2AServerORM).where(A2AServerORM.id == record.id))
        existing = result.scalar_one_or_none()
        record.updated_at = datetime.now()
        if existing:
            existing.base_url = record.base_url
            existing.enabled = record.enabled
            existing.owner_user_id = record.owner_user_id
            existing.visibility = record.visibility.value if hasattr(record.visibility, "value") else record.visibility
            existing.updated_at = record.updated_at
        else:
            self.db_session.add(
                A2AServerORM(
                    id=record.id,
                    base_url=record.base_url,
                    enabled=record.enabled,
                    owner_user_id=record.owner_user_id,
                    visibility=record.visibility.value if hasattr(record.visibility, "value") else record.visibility,
                )
            )

    async def delete_by_id(self, server_id: str) -> None:
        await self.db_session.execute(delete(A2AServerORM).where(A2AServerORM.id == server_id))
