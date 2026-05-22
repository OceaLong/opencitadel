#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.llm_model import LLMModel
from app.domain.repositories.llm_model_repository import LLMModelRepository
from app.infrastructure.models.llm_model import LLMModelORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


class DBLLMModelRepository(LLMModelRepository):
    def __init__(self, db_session: AsyncSession, cipher: ApiKeyCipher) -> None:
        self.db_session = db_session
        self.cipher = cipher

    def _decrypt_key(self, encrypted: str) -> str:
        return self.cipher.decrypt(encrypted)

    async def get_all(self) -> List[LLMModel]:
        stmt = select(LLMModelORM).order_by(LLMModelORM.is_default.desc(), LLMModelORM.created_at)
        result = await self.db_session.execute(stmt)
        return [r.to_domain(self._decrypt_key(r.api_key)) for r in result.scalars().all()]

    async def get_by_id(self, model_id: str) -> Optional[LLMModel]:
        stmt = select(LLMModelORM).where(LLMModelORM.id == model_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain(self._decrypt_key(record.api_key)) if record else None

    async def get_default(self) -> Optional[LLMModel]:
        stmt = select(LLMModelORM).where(LLMModelORM.is_default.is_(True)).limit(1)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            return record.to_domain(self._decrypt_key(record.api_key))
        stmt = select(LLMModelORM).order_by(LLMModelORM.created_at).limit(1)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain(self._decrypt_key(record.api_key)) if record else None

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
            record.model_name = model.model_name
            record.temperature = model.temperature
            record.max_tokens = model.max_tokens
            record.extra_params = model.extra_params
            record.supports_multimodal = model.supports_multimodal
            record.is_default = model.is_default
            record.updated_at = model.updated_at
        else:
            self.db_session.add(LLMModelORM.from_domain(model, encrypted_api_key))

    async def delete_by_id(self, model_id: str) -> None:
        await self.db_session.execute(delete(LLMModelORM).where(LLMModelORM.id == model_id))

    async def clear_default(self) -> None:
        await self.db_session.execute(update(LLMModelORM).values(is_default=False))

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(LLMModelORM))
        return result.scalar() or 0
