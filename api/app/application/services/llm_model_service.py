#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable, List, Optional

from app.application.errors.exceptions import NotFoundError, BadRequestError
from app.domain.models.llm_model import LLMModel
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher

logger = logging.getLogger(__name__)


class LLMModelService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork], cipher: ApiKeyCipher) -> None:
        self._uow_factory = uow_factory
        self._cipher = cipher

    def _mask(self, model: LLMModel) -> LLMModel:
        masked = model.mask_api_key()
        masked.api_key = ApiKeyCipher.mask(model.api_key)
        return masked

    async def list_models(self, mask: bool = True) -> List[LLMModel]:
        async with self._uow_factory() as uow:
            models = await uow.llm_model.get_all()
        return [self._mask(m) if mask else m for m in models]

    async def get_model(self, model_id: str, mask: bool = True) -> LLMModel:
        async with self._uow_factory() as uow:
            model = await uow.llm_model.get_by_id(model_id)
        if not model:
            raise NotFoundError(f"模型[{model_id}]不存在")
        return self._mask(model) if mask else model

    async def get_default_model(self) -> Optional[LLMModel]:
        async with self._uow_factory() as uow:
            return await uow.llm_model.get_default()

    async def resolve_model(self, model_id: Optional[str] = None) -> LLMModel:
        async with self._uow_factory() as uow:
            if model_id:
                model = await uow.llm_model.get_by_id(model_id)
                if model:
                    return model
            model = await uow.llm_model.get_default()
        if not model:
            raise BadRequestError("未配置任何LLM模型，请先在设置中添加模型")
        return model

    async def create_model(self, model: LLMModel) -> LLMModel:
        encrypted = self._cipher.encrypt(model.api_key) if model.api_key else ""
        async with self._uow_factory() as uow:
            if model.is_default:
                await uow.llm_model.clear_default()
            await uow.llm_model.save(model, encrypted)
        return self._mask(model)

    async def update_model(self, model_id: str, updates: LLMModel) -> LLMModel:
        async with self._uow_factory() as uow:
            existing = await uow.llm_model.get_by_id(model_id)
            if not existing:
                raise NotFoundError(f"模型[{model_id}]不存在")
            updates.id = model_id
            if not updates.api_key.strip():
                updates.api_key = existing.api_key
            encrypted = self._cipher.encrypt(updates.api_key) if updates.api_key else ""
            if updates.is_default:
                await uow.llm_model.clear_default()
            await uow.llm_model.save(updates, encrypted)
        return self._mask(updates)

    async def delete_model(self, model_id: str) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.llm_model.get_by_id(model_id)
            if not existing:
                raise NotFoundError(f"模型[{model_id}]不存在")
            count = await uow.llm_model.count()
            if count <= 1:
                raise BadRequestError("至少保留一个模型配置")
            was_default = existing.is_default
            await uow.llm_model.delete_by_id(model_id)
            if was_default:
                models = await uow.llm_model.get_all()
                if models:
                    models[0].is_default = True
                    await uow.llm_model.clear_default()
                    # 仅更新默认标记，保留数据库中已加密的 api_key
                    await uow.llm_model.save(models[0], "")

    async def set_default(self, model_id: str) -> LLMModel:
        async with self._uow_factory() as uow:
            model = await uow.llm_model.get_by_id(model_id)
            if not model:
                raise NotFoundError(f"模型[{model_id}]不存在")
            await uow.llm_model.clear_default()
            model.is_default = True
            await uow.llm_model.save(model, self._cipher.encrypt(model.api_key))
        return self._mask(model)

    async def sync_from_llm_config(self, llm_config) -> LLMModel:
        """从config.yaml迁移默认模型"""
        model = LLMModel.from_llm_config(llm_config)
        async with self._uow_factory() as uow:
            count = await uow.llm_model.count()
            if count > 0:
                return await uow.llm_model.get_default()
            await uow.llm_model.save(model, self._cipher.encrypt(model.api_key))
        return model
