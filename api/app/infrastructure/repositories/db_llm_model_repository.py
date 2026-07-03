#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import or_, select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.llm_model import LLMModel, LLMProvider
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.llm_model_repository import LLMModelRepository
from app.infrastructure.models.llm_endpoint import LLMEndpointORM
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

    def _model_stmt(self, scope: Optional[OwnerScope] = None):
        stmt = select(LLMModelORM, LLMEndpointORM).join(
            LLMEndpointORM,
            LLMModelORM.endpoint_id == LLMEndpointORM.id,
        )
        return self._apply_scope(stmt, scope)

    def _to_domain(self, model_record: LLMModelORM, endpoint_record: LLMEndpointORM) -> LLMModel:
        api_key = self._resolve_api_key(endpoint_record.api_key, endpoint_record.api_key_encryption)
        return model_record.to_domain(
            provider=LLMProvider(endpoint_record.provider),
            base_url=endpoint_record.base_url,
            api_key=api_key,
        )

    async def get_all(self, scope: Optional[OwnerScope] = None) -> List[LLMModel]:
        stmt = self._model_stmt(scope).order_by(
            LLMModelORM.is_default.desc(),
            LLMModelORM.created_at,
        )
        result = await self.db_session.execute(stmt)
        return [self._to_domain(model, endpoint) for model, endpoint in result.all()]

    async def get_by_id(self, model_id: str, scope: Optional[OwnerScope] = None) -> Optional[LLMModel]:
        stmt = self._model_stmt(scope).where(LLMModelORM.id == model_id)
        result = await self.db_session.execute(stmt)
        row = result.first()
        if not row:
            return None
        model_record, endpoint_record = row
        return self._to_domain(model_record, endpoint_record)

    async def get_default(self) -> Optional[LLMModel]:
        stmt = (
            select(LLMModelORM, LLMEndpointORM)
            .join(LLMEndpointORM, LLMModelORM.endpoint_id == LLMEndpointORM.id)
            .where(LLMModelORM.is_default.is_(True))
            .limit(1)
        )
        result = await self.db_session.execute(stmt)
        row = result.first()
        if row:
            return self._to_domain(row[0], row[1])
        stmt = (
            select(LLMModelORM, LLMEndpointORM)
            .join(LLMEndpointORM, LLMModelORM.endpoint_id == LLMEndpointORM.id)
            .order_by(LLMModelORM.created_at)
            .limit(1)
        )
        result = await self.db_session.execute(stmt)
        row = result.first()
        if not row:
            return None
        return self._to_domain(row[0], row[1])

    async def get_by_endpoint_id(self, endpoint_id: str, scope: Optional[OwnerScope] = None) -> List[LLMModel]:
        stmt = self._model_stmt(scope).where(LLMModelORM.endpoint_id == endpoint_id).order_by(
            LLMModelORM.created_at
        )
        result = await self.db_session.execute(stmt)
        return [self._to_domain(model, endpoint) for model, endpoint in result.all()]

    async def save(self, model: LLMModel) -> None:
        stmt = select(LLMModelORM).where(LLMModelORM.id == model.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        model.updated_at = datetime.now()
        if record:
            record.endpoint_id = model.endpoint_id
            record.display_name = model.display_name
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
            self.db_session.add(LLMModelORM.from_domain(model))

    async def delete_by_id(self, model_id: str) -> None:
        await self.db_session.execute(delete(LLMModelORM).where(LLMModelORM.id == model_id))

    async def clear_default(self) -> None:
        await self.db_session.execute(update(LLMModelORM).values(is_default=False))

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(LLMModelORM))
        return int(result.scalar() or 0)

    async def count_by_endpoint_id(self, endpoint_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(LLMModelORM)
            .where(LLMModelORM.endpoint_id == endpoint_id)
        )
        result = await self.db_session.execute(stmt)
        return int(result.scalar() or 0)
