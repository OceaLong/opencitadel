#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import or_, select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.llm_model import LLMModel
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.llm_model_repository import LLMModelRepository
from app.infrastructure.models.llm_model import LLMModelORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher, ApiKeyCipherError
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class DBLLMModelRepository(LLMModelRepository):
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
            LLMModelORM.owner_user_id == scope.user_id
            if scope.type == OwnerScopeType.PERSONAL
            else LLMModelORM.owner_user_id == scope.user_id
        )
        return stmt.where(or_(LLMModelORM.visibility == "global", owner_filter))

    async def get_all(self, scope: Optional[OwnerScope] = None) -> List[LLMModel]:
        stmt = self._apply_scope(select(LLMModelORM), scope).order_by(LLMModelORM.is_default.desc(), LLMModelORM.created_at)
        result = await self.db_session.execute(stmt)
        return [
            r.to_domain(self._resolve_api_key(r.api_key, r.api_key_encryption))
            for r in result.scalars().all()
        ]

    async def get_by_id(self, model_id: str, scope: Optional[OwnerScope] = None) -> Optional[LLMModel]:
        stmt = self._apply_scope(select(LLMModelORM).where(LLMModelORM.id == model_id), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return None
        return record.to_domain(self._resolve_api_key(record.api_key, record.api_key_encryption))

    async def get_default(self) -> Optional[LLMModel]:
        stmt = select(LLMModelORM).where(LLMModelORM.is_default.is_(True)).limit(1)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            return record.to_domain(self._resolve_api_key(record.api_key, record.api_key_encryption))
        stmt = select(LLMModelORM).order_by(LLMModelORM.created_at).limit(1)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return None
        return record.to_domain(self._resolve_api_key(record.api_key, record.api_key_encryption))

    async def save(self, model: LLMModel, encrypted_api_key: str) -> None:
        stmt = select(LLMModelORM).where(LLMModelORM.id == model.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        model.updated_at = datetime.now()
        if record:
            record.display_name = model.display_name
            record.provider = model.provider.value
            record.base_url = model.base_url
            if encrypted_api_key:
                record.api_key = encrypted_api_key
                record.api_key_encryption = ApiKeyEncryption.FERNET_V1
            record.model_name = model.model_name
            record.temperature = model.temperature
            record.max_tokens = model.max_tokens
            record.input_price_per_million = model.input_price_per_million
            record.output_price_per_million = model.output_price_per_million
            record.extra_params = model.extra_params
            record.capabilities = model.capabilities.model_dump()
            record.supports_multimodal = model.capabilities.vision
            record.is_default = model.is_default
            record.owner_user_id = model.owner_user_id
            record.visibility = model.visibility.value if hasattr(model.visibility, "value") else model.visibility
            record.updated_at = model.updated_at
        else:
            encryption = (
                ApiKeyEncryption.FERNET_V1
                if encrypted_api_key
                else ApiKeyEncryption.LEGACY_PLAINTEXT
            )
            self.db_session.add(
                LLMModelORM.from_domain(model, encrypted_api_key, api_key_encryption=encryption)
            )

    async def delete_by_id(self, model_id: str) -> None:
        await self.db_session.execute(delete(LLMModelORM).where(LLMModelORM.id == model_id))

    async def clear_default(self) -> None:
        await self.db_session.execute(update(LLMModelORM).values(is_default=False))

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(LLMModelORM))
        return result.scalar() or 0
