#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable, List, Optional

from app.domain.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.llm_model import LLMProvider, ResourceVisibility
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.outbound_url import (
    OutboundURLRejected,
    resolve_outbound_url,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from core.config import get_settings

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
        settings = get_settings()
        try:
            resolve_outbound_url(
                endpoint.base_url,
                allowed_ports={
                    int(item.strip())
                    for item in settings.outbound_allowed_ports.split(",")
                    if item.strip()
                },
                allow_private_hosts={
                    item.strip()
                    for item in settings.outbound_private_host_allowlist.split(",")
                    if item.strip()
                },
                resolve_dns=False,
            )
        except (OutboundURLRejected, ValueError) as exc:
            raise BadRequestError(f"端点 Base URL 未通过出站安全策略: {exc}") from exc
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

    @staticmethod
    def _bind_ownership(endpoint: LLMEndpoint, scope: Optional[OwnerScope]) -> None:
        visibility = endpoint.visibility.value if hasattr(endpoint.visibility, "value") else endpoint.visibility
        if visibility == ResourceVisibility.GLOBAL.value:
            endpoint.owner_user_id = None
            endpoint.team_id = None
            return
        if scope is None:
            raise BadRequestError("私有端点必须绑定访问作用域")
        endpoint.owner_user_id = scope.user_id
        endpoint.team_id = scope.team_id if scope.type == OwnerScopeType.TEAM else None

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

    async def create_endpoint(
        self,
        endpoint: LLMEndpoint,
        scope: Optional[OwnerScope] = None,
        *,
        allow_global_mutation: bool = False,
    ) -> LLMEndpoint:
        if (
            endpoint.visibility == ResourceVisibility.GLOBAL
            and not allow_global_mutation
        ):
            raise ForbiddenError("只有管理员可创建全局端点")
        self._bind_ownership(endpoint, scope)
        self._validate_endpoint(endpoint, require_api_key=endpoint.provider != LLMProvider.OLLAMA)
        encrypted = (
            self._cipher.encrypt_versioned(endpoint.api_key)
            if endpoint.api_key
            else ""
        )
        async with self._uow_factory() as uow:
            await uow.llm_endpoint.save(endpoint, encrypted)
        return self._mask(endpoint)

    async def update_endpoint(
        self,
        endpoint_id: str,
        updates: LLMEndpoint,
        scope: Optional[OwnerScope] = None,
        *,
        allow_global_mutation: bool = False,
    ) -> LLMEndpoint:
        async with self._uow_factory() as uow:
            existing = await uow.llm_endpoint.get_by_id(endpoint_id, scope=scope)
            if not existing:
                raise NotFoundError(f"端点[{endpoint_id}]不存在")
            if (
                updates.visibility == ResourceVisibility.GLOBAL
                and not allow_global_mutation
            ):
                raise ForbiddenError("只有管理员可修改全局端点")
            if existing.visibility != updates.visibility:
                raise BadRequestError("端点可见性不可通过更新修改，请新建端点并迁移模型")
            if (
                existing.visibility == ResourceVisibility.GLOBAL
                and not allow_global_mutation
            ):
                raise ForbiddenError("只有管理员可修改全局端点")
            updates.id = endpoint_id
            if not updates.api_key.strip() or "****" in updates.api_key:
                updates.api_key = existing.api_key
            self._bind_ownership(updates, scope)
            self._validate_endpoint(updates)
            encrypted = (
                self._cipher.encrypt_versioned(updates.api_key)
                if updates.api_key
                else ""
            )
            await uow.llm_endpoint.save(updates, encrypted)
        return self._mask(updates)

    async def delete_endpoint(
        self,
        endpoint_id: str,
        scope: Optional[OwnerScope] = None,
        *,
        allow_global_mutation: bool = False,
    ) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.llm_endpoint.get_by_id(endpoint_id, scope=scope)
            if not existing:
                raise NotFoundError(f"端点[{endpoint_id}]不存在")
            if (
                existing.visibility == ResourceVisibility.GLOBAL
                and not allow_global_mutation
            ):
                raise ForbiddenError("只有管理员可删除全局端点")
            model_count = await uow.llm_endpoint.count_models(endpoint_id)
            if model_count > 0:
                raise BadRequestError("请先删除或迁移该端点下的所有模型")
            await uow.llm_endpoint.delete_by_id(endpoint_id)
