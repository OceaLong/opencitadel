#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import or_, select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.llm_endpoint_repository import LLMEndpointRepository
from app.infrastructure.models.llm_endpoint import LLMEndpointORM
from app.infrastructure.models.llm_model import LLMModelORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher, ApiKeyCipherError
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class DBLLMEndpointRepository(LLMEndpointRepository):
    def __init__(self, db_session: AsyncSession, cipher: ApiKeyCipher) -> None:
        self.db_session = db_session
        self.cipher = cipher

    def _resolve_api_key(self, stored: str, encryption: str) -> str:
        if not stored:
            return ""
        if encryption == ApiKeyEncryption.LEGACY_PLAINTEXT:
            return stored
        if encryption == ApiKeyEncryption.FERNET_V1:
            return self.cipher.decrypt_or_raise(stored)
        raise ApiKeyCipherError(f"未知的 api_key_encryption 格式: {encryption}")

    def _apply_scope(self, stmt, scope: Optional[OwnerScope]):
        if scope is None:
            return stmt
        owner_filter = (
            LLMEndpointORM.owner_user_id == scope.user_id
            if scope.type == OwnerScopeType.PERSONAL
            else LLMEndpointORM.owner_user_id == scope.user_id
        )
        return stmt.where(or_(LLMEndpointORM.visibility == "global", owner_filter))

    async def get_all(self, scope: Optional[OwnerScope] = None) -> List[LLMEndpoint]:
        stmt = self._apply_scope(select(LLMEndpointORM), scope).order_by(
            LLMEndpointORM.created_at
        )
        result = await self.db_session.execute(stmt)
        return [
            r.to_domain(self._resolve_api_key(r.api_key, r.api_key_encryption))
            for r in result.scalars().all()
        ]

    async def get_by_id(self, endpoint_id: str, scope: Optional[OwnerScope] = None) -> Optional[LLMEndpoint]:
        stmt = self._apply_scope(
            select(LLMEndpointORM).where(LLMEndpointORM.id == endpoint_id),
            scope,
        )
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return None
        return record.to_domain(self._resolve_api_key(record.api_key, record.api_key_encryption))

    async def save(self, endpoint: LLMEndpoint, encrypted_api_key: str) -> None:
        stmt = select(LLMEndpointORM).where(LLMEndpointORM.id == endpoint.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        endpoint.updated_at = datetime.now()
        if record:
            record.display_name = endpoint.display_name
            record.provider = endpoint.provider.value
            record.base_url = endpoint.base_url
            if encrypted_api_key:
                record.api_key = encrypted_api_key
                record.api_key_encryption = ApiKeyEncryption.FERNET_V1
            record.owner_user_id = endpoint.owner_user_id
            record.visibility = endpoint.visibility.value if hasattr(endpoint.visibility, "value") else endpoint.visibility
            record.updated_at = endpoint.updated_at
        else:
            encryption = (
                ApiKeyEncryption.FERNET_V1
                if encrypted_api_key
                else ApiKeyEncryption.LEGACY_PLAINTEXT
            )
            self.db_session.add(
                LLMEndpointORM.from_domain(endpoint, encrypted_api_key, api_key_encryption=encryption)
            )

    async def delete_by_id(self, endpoint_id: str) -> None:
        await self.db_session.execute(delete(LLMEndpointORM).where(LLMEndpointORM.id == endpoint_id))

    async def count_models(self, endpoint_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(LLMModelORM)
            .where(LLMModelORM.endpoint_id == endpoint_id)
        )
        result = await self.db_session.execute(stmt)
        return int(result.scalar() or 0)
