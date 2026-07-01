#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import base64
import logging
from typing import Callable, List, Optional

from app.application.errors.exceptions import NotFoundError, BadRequestError, ServerRequestsError
from app.domain.models.llm_model import LLMModel, LLMProvider, ModelCapabilities, ResourceVisibility
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.external.llm.factory import LLMFactory
from app.infrastructure.security.api_key_cipher import ApiKeyCipher

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = {
    LLMProvider.OPENAI,
    LLMProvider.OLLAMA,
    LLMProvider.AZURE,
    LLMProvider.ANTHROPIC,
    LLMProvider.GEMINI,
}


class LLMModelService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork], cipher: ApiKeyCipher) -> None:
        self._uow_factory = uow_factory
        self._cipher = cipher

    def _validate_model(self, model: LLMModel, *, require_api_key: bool = False) -> None:
        if not model.display_name.strip():
            raise BadRequestError("模型显示名称不能为空")
        if not model.model_name.strip():
            raise BadRequestError("模型名称(model_name)不能为空")
        if not model.base_url.strip():
            raise BadRequestError("模型 Base URL 不能为空")
        if model.provider not in _SUPPORTED_PROVIDERS:
            raise BadRequestError(
                f"Provider「{model.provider.value}」尚未实现，"
                f"请使用 OpenAI/Ollama/Azure/Anthropic/Gemini"
            )
        if require_api_key and not model.api_key.strip():
            raise BadRequestError("API Key 不能为空")

    def _ensure_invokable(self, model: LLMModel) -> None:
        self._validate_model(model)
        if model.provider != LLMProvider.OLLAMA and not model.api_key.strip():
            raise BadRequestError(
                f"模型「{model.display_name}」未配置 API Key，请在设置中补充后再调用"
            )

    def _mask(self, model: LLMModel) -> LLMModel:
        masked = model.mask_api_key()
        masked.api_key = ApiKeyCipher.mask(model.api_key)
        return masked

    async def list_models(self, mask: bool = True, scope: Optional[OwnerScope] = None) -> List[LLMModel]:
        async with self._uow_factory() as uow:
            models = await uow.llm_model.get_all(scope=scope)
        return [self._mask(m) if mask else m for m in models]

    async def get_model(self, model_id: str, mask: bool = True, scope: Optional[OwnerScope] = None) -> LLMModel:
        async with self._uow_factory() as uow:
            model = await uow.llm_model.get_by_id(model_id, scope=scope)
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
                    self._ensure_invokable(model)
                    return model
            model = await uow.llm_model.get_default()
        if not model:
            raise BadRequestError("未配置任何LLM模型，请先在设置中添加模型")
        self._ensure_invokable(model)
        return model

    async def create_model(self, model: LLMModel, scope: Optional[OwnerScope] = None) -> LLMModel:
        visibility = model.visibility.value if hasattr(model.visibility, "value") else model.visibility
        if scope is not None and visibility != "global":
            model.owner_user_id = scope.user_id
        self._validate_model(model, require_api_key=model.provider != LLMProvider.OLLAMA)
        encrypted = self._cipher.encrypt(model.api_key) if model.api_key else ""
        async with self._uow_factory() as uow:
            count = await uow.llm_model.count()
            if count == 0:
                model.is_default = True
            if model.is_default:
                await uow.llm_model.clear_default()
            await uow.llm_model.save(model, encrypted)
        return self._mask(model)

    async def update_model(self, model_id: str, updates: LLMModel, scope: Optional[OwnerScope] = None) -> LLMModel:
        async with self._uow_factory() as uow:
            existing = await uow.llm_model.get_by_id(model_id, scope=scope)
            if not existing:
                raise NotFoundError(f"模型[{model_id}]不存在")
            updates.id = model_id
            if not updates.api_key.strip() or "****" in updates.api_key:
                updates.api_key = existing.api_key
            self._validate_model(updates)
            encrypted = self._cipher.encrypt(updates.api_key) if updates.api_key else ""
            if updates.is_default:
                await uow.llm_model.clear_default()
            await uow.llm_model.save(updates, encrypted)
        return self._mask(updates)

    async def delete_model(self, model_id: str, scope: Optional[OwnerScope] = None) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.llm_model.get_by_id(model_id, scope=scope)
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
            if model.visibility != ResourceVisibility.GLOBAL:
                raise BadRequestError("只有全局模型可设为系统默认")
            self._validate_model(model)
            await uow.llm_model.clear_default()
            model.is_default = True
            await uow.llm_model.save(model, "")
        return self._mask(model)

    async def probe_multimodal(self, model_id: str) -> dict:
        model = await self.get_model(model_id, mask=False)
        self._ensure_invokable(model)
        probe = await self._run_vision_probe(model)
        if probe.get("status") == "ok":
            caps = model.capabilities.model_copy(update={"vision": True})
            if probe.get("vision_with_tools") is False:
                caps = caps.model_copy(update={"vision_with_tools": False})
            model = model.model_copy(update={"capabilities": caps, "supports_multimodal": True})
        elif probe.get("status") == "error":
            caps = model.capabilities.model_copy(update={"vision": False})
            model = model.model_copy(update={"capabilities": caps, "supports_multimodal": False})
        async with self._uow_factory() as uow:
            encrypted = self._cipher.encrypt(model.api_key) if model.api_key else ""
            await uow.llm_model.save(model, encrypted)
        return probe

    async def _auto_probe_capabilities(self, model: LLMModel) -> LLMModel:
        """保存模型时自动探测 vision / vision_with_tools 能力。"""
        if model.provider == LLMProvider.OLLAMA:
            return model
        try:
            self._ensure_invokable(model)
        except BadRequestError:
            return model

        if model.extra_params.get("skip_capability_probe"):
            return model

        caps = model.capabilities
        if not (caps.vision or model.supports_multimodal):
            return model

        probe = await self._run_vision_probe(model)
        if probe.get("status") == "ok":
            caps = caps.model_copy(update={"vision": True})
            if probe.get("vision_with_tools") is False:
                caps = caps.model_copy(update={"vision_with_tools": False})
        elif probe.get("status") == "error":
            caps = caps.model_copy(update={"vision": False})
        model = model.model_copy(update={"capabilities": caps})
        model.supports_multimodal = caps.vision
        return model

    async def _run_vision_probe(self, model: LLMModel) -> dict:
        if not model.capabilities.vision and not model.supports_multimodal:
            return {"status": "skipped", "message": "模型未开启多模态能力"}

        llm = LLMFactory.create(model)
        one_pixel_png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
            "AAAABJRU5ErkJggg=="
        )
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "probe"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{one_pixel_png}"},
                },
            ],
        }]
        try:
            result = await llm.invoke(messages)
            if result.get("content") is not None or result.get("tool_calls"):
                probe_tools = {"status": "ok", "message": "多模态探测成功", "vision_with_tools": True}
                try:
                    tool_result = await llm.invoke(
                        messages,
                        tools=[{
                            "type": "function",
                            "function": {
                                "name": "probe_tool",
                                "description": "probe",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }],
                    )
                    if tool_result.get("tool_calls"):
                        probe_tools["vision_with_tools"] = True
                    else:
                        probe_tools["vision_with_tools"] = bool(tool_result.get("content"))
                except Exception:
                    probe_tools["vision_with_tools"] = False
                return probe_tools
            return {"status": "fallback", "message": "模型返回空内容"}
        except ServerRequestsError as exc:
            message = getattr(exc, "msg", None) or str(exc)
            return {"status": "error", "message": message, "error_code": "server_error"}
        except Exception as exc:
            return {"status": "error", "message": str(exc), "error_code": type(exc).__name__}
