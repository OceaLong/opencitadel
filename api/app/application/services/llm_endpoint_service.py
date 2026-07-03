#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable, List, Optional

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.llm_model import LLMProvider, ResourceVisibility
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = {
    LLMProvider.OPENAI,
    LLMProvider.OLLAMA,
    LLMProvider.AZURE,
    LLMProvider.ANTHROPIC,
    LLMProvider.GEMINI,
}


class LLMEndpointService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork], cipher: ApiKeyCipher) -> None:
        self._uow_factory = uow_factory
        self._cipher = cipher

    def _validate_endpoint(self, endpoint: LLMEndpoint, *, require_api_key: bool = False) -> None:
        if not endpoint.display_name.strip():
            raise BadRequestError("端点显示名称不能为空")
        if not endpoint.base_url.strip():
            raise BadRequestError("端点 Base URL 不能为空")
        if endpoint.provider not in _SUPPORTED_PROVIDERS:
            raise BadRequestError(
                f"Provider「{endpoint.provider.value}」尚未实现，"
                f"请使用 OpenAI/Ollama/Azure/Anthropic/Gemini"
            )
        if require_api_key and endpoint.provider != LLMProvider.OLLAMA and not endpoint.api_key.strip():
            raise BadRequestError("API Key 不能为空")

    def _mask(self, endpoint: LLMEndpoint) -> LLMEndpoint:
        masked = endpoint.mask_api_key()
        masked.api_key = ApiKeyCipher.mask(endpoint.api_key)
        return masked

    async def list_endpoints(self, scope: Optional[OwnerScope] = None) -> List[LLMEndpoint]:
        async with self._uow_factory() as uow:
            endpoints = await uow.llm_endpoint.get_all(scope=scope)
            return [self._mask(endpoint) for endpoint in endpoints]

    async def count_models(self, endpoint_id: str) -> int:
        async with self._uow_factory() as uow:
            return await uow.llm_endpoint.count_models(endpoint_id)

    async def get_endpoint(
        self,
        endpoint_id: str,
        *,
        mask: bool = True,
        scope: Optional[OwnerScope] = None,
    ) -> LLMEndpoint:
        async with self._uow_factory() as uow:
            endpoint = await uow.llm_endpoint.get_by_id(endpoint_id, scope=scope)
        if not endpoint:
            raise NotFoundError(f"端点[{endpoint_id}]不存在")
        return self._mask(endpoint) if mask else endpoint

    async def create_endpoint(self, endpoint: LLMEndpoint, scope: Optional[OwnerScope] = None) -> LLMEndpoint:
        visibility = endpoint.visibility.value if hasattr(endpoint.visibility, "value") else endpoint.visibility
        if scope is not None and visibility != "global":
            endpoint.owner_user_id = scope.user_id
        self._validate_endpoint(endpoint, require_api_key=endpoint.provider != LLMProvider.OLLAMA)
        encrypted = self._cipher.encrypt(endpoint.api_key) if endpoint.api_key else ""
        async with self._uow_factory() as uow:
            await uow.llm_endpoint.save(endpoint, encrypted)
        return self._mask(endpoint)

    async def update_endpoint(
        self,
        endpoint_id: str,
        updates: LLMEndpoint,
        scope: Optional[OwnerScope] = None,
    ) -> LLMEndpoint:
        async with self._uow_factory() as uow:
            existing = await uow.llm_endpoint.get_by_id(endpoint_id, scope=scope)
            if not existing:
                raise NotFoundError(f"端点[{endpoint_id}]不存在")
            updates.id = endpoint_id
            if not updates.api_key.strip() or "****" in updates.api_key:
                updates.api_key = existing.api_key
            self._validate_endpoint(updates)
            encrypted = self._cipher.encrypt(updates.api_key) if updates.api_key else ""
            await uow.llm_endpoint.save(updates, encrypted)
        return self._mask(updates)

    async def delete_endpoint(self, endpoint_id: str, scope: Optional[OwnerScope] = None) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.llm_endpoint.get_by_id(endpoint_id, scope=scope)
            if not existing:
                raise NotFoundError(f"端点[{endpoint_id}]不存在")
            model_count = await uow.llm_endpoint.count_models(endpoint_id)
            if model_count > 0:
                raise BadRequestError("请先删除或迁移该端点下的所有模型")
            await uow.llm_endpoint.delete_by_id(endpoint_id)
