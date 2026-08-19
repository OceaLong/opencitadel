#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable, List, Optional

from app.domain.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ServerRequestsError,
)
from app.domain.models.llm_model import LLMModel, LLMProvider, ResourceVisibility
from app.domain.models.scope import OwnerScope, OwnerScopeType
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

    def _validate_model(self, model: LLMModel) -> None:
        if not model.endpoint_id.strip():
            raise BadRequestError("必须选择 LLM 端点")
        if not model.display_name.strip():
            raise BadRequestError("模型显示名称不能为空")
        if not model.model_name.strip():
            raise BadRequestError("模型名称(model_name)不能为空")
        if model.provider not in _SUPPORTED_PROVIDERS:
            raise BadRequestError(
                f"Provider「{model.provider.value}」尚未实现，"
                f"请使用 OpenAI/Ollama/Azure/Anthropic/Gemini"
            )

    def _ensure_invokable(self, model: LLMModel) -> None:
        self._validate_model(model)
        if model.provider != LLMProvider.OLLAMA and not model.api_key.strip():
            raise BadRequestError(
                f"模型「{model.display_name}」所属端点未配置 API Key，请在设置中补充后再调用"
            )

    def _mask(self, model: LLMModel) -> LLMModel:
        masked = model.mask_api_key()
        masked.api_key = ApiKeyCipher.mask(model.api_key)
        return masked

    @staticmethod
    def _project_preference(model: LLMModel, preferred_model_id: Optional[str]) -> LLMModel:
        return model.model_copy(
            update={"is_default": model.id == preferred_model_id}
        )

    async def _get_preferred_model(
            self,
            uow: IUnitOfWork,
            scope: Optional[OwnerScope],
    ) -> Optional[LLMModel]:
        if scope is not None:
            scoped_model_id = await uow.llm_model_preference.get_model_id(scope)
            if scoped_model_id:
                scoped_model = await uow.llm_model.get_by_id(
                    scoped_model_id,
                    scope=scope,
                )
                if scoped_model:
                    return scoped_model

        global_model_id = await uow.llm_model_preference.get_model_id(None)
        if global_model_id:
            global_model = await uow.llm_model.get_by_id(
                global_model_id,
                scope=scope,
            )
            if (
                global_model
                and global_model.visibility == ResourceVisibility.GLOBAL
            ):
                return global_model

        # Transitional fallback for databases not yet upgraded to the
        # preference table. The migration seeds this binding and clears the
        # legacy flags.
        legacy_default = await uow.llm_model.get_default()
        if (
            legacy_default
            and legacy_default.visibility == ResourceVisibility.GLOBAL
        ):
            return legacy_default
        return None

    @staticmethod
    def _bind_ownership(model: LLMModel, scope: Optional[OwnerScope]) -> None:
        visibility = model.visibility.value if hasattr(model.visibility, "value") else model.visibility
        if visibility == ResourceVisibility.GLOBAL.value:
            model.owner_user_id = None
            model.team_id = None
            return
        if scope is None:
            raise BadRequestError("私有模型必须绑定访问作用域")
        model.owner_user_id = scope.user_id
        model.team_id = scope.team_id if scope.type == OwnerScopeType.TEAM else None

    async def list_models(self, mask: bool = True, scope: Optional[OwnerScope] = None) -> List[LLMModel]:
        async with self._uow_factory() as uow:
            models = await uow.llm_model.get_all(scope=scope)
            preferred = await self._get_preferred_model(uow, scope)
        projected = [
            self._project_preference(model, preferred.id if preferred else None)
            for model in models
        ]
        return [self._mask(model) if mask else model for model in projected]

    async def get_model(self, model_id: str, mask: bool = True, scope: Optional[OwnerScope] = None) -> LLMModel:
        async with self._uow_factory() as uow:
            model = await uow.llm_model.get_by_id(model_id, scope=scope)
            preferred = await self._get_preferred_model(uow, scope)
        if not model:
            raise NotFoundError(f"模型[{model_id}]不存在")
        model = self._project_preference(
            model,
            preferred.id if preferred else None,
        )
        return self._mask(model) if mask else model

    async def get_default_model(
        self,
        scope: Optional[OwnerScope] = None,
    ) -> Optional[LLMModel]:
        async with self._uow_factory() as uow:
            model = await self._get_preferred_model(uow, scope)
        if not model:
            return None
        return self._project_preference(model, model.id)

    async def resolve_model(
            self,
            model_id: Optional[str] = None,
            *,
            scope: Optional[OwnerScope] = None,
    ) -> LLMModel:
        if model_id and scope is None:
            raise BadRequestError("显式模型解析必须提供访问作用域")
        async with self._uow_factory() as uow:
            if model_id:
                model = await uow.llm_model.get_by_id(model_id, scope=scope)
                if not model:
                    raise NotFoundError(f"模型[{model_id}]不存在或无权访问")
                self._ensure_invokable(model)
                return model
            model = await self._get_preferred_model(uow, scope)
        if not model:
            raise BadRequestError("未配置任何LLM模型，请先在设置中添加模型")
        self._ensure_invokable(model)
        return model

    async def resolve_vision_model(
        self,
        scope: Optional[OwnerScope] = None,
    ) -> Optional[LLMModel]:
        async with self._uow_factory() as uow:
            default = await self._get_preferred_model(uow, scope)
            models = (
                await uow.llm_model.get_all(scope=scope)
                if scope is not None
                else await uow.llm_model.get_all_global()
            )
        candidates: list[LLMModel] = []
        if default:
            candidates.append(default)
        candidates.extend(model for model in models if model.id != (default.id if default else None))
        for model in candidates:
            if not (model.capabilities.vision or model.supports_multimodal):
                continue
            try:
                self._ensure_invokable(model)
                return model
            except BadRequestError:
                continue
        return None

    async def create_model(
            self,
            model: LLMModel,
            scope: Optional[OwnerScope] = None,
            *,
            allow_global_mutation: bool = False,
    ) -> LLMModel:
        visibility = model.visibility.value if hasattr(model.visibility, "value") else model.visibility
        if model.is_default:
            raise BadRequestError("请使用专用接口修改系统默认模型")
        if visibility == ResourceVisibility.GLOBAL.value and not allow_global_mutation:
            raise ForbiddenError("只有管理员可创建全局模型")
        self._bind_ownership(model, scope)
        async with self._uow_factory() as uow:
            endpoint = await uow.llm_endpoint.get_by_id(model.endpoint_id, scope=scope)
            if not endpoint:
                raise BadRequestError(f"端点[{model.endpoint_id}]不存在或不可访问")
            model = model.model_copy(
                update={
                    "provider": endpoint.provider,
                    "base_url": endpoint.base_url,
                    "api_key": endpoint.api_key,
                }
            )
            self._validate_model(model)
            global_count = await uow.llm_model.count_global()
            becomes_system_default = (
                global_count == 0
                and visibility == ResourceVisibility.GLOBAL.value
            )
            model.is_default = False
            await uow.llm_model.save(model)
            if becomes_system_default:
                await uow.llm_model_preference.set_model_id(None, model.id)
        return self._mask(
            self._project_preference(
                model,
                model.id if becomes_system_default else None,
            )
        )

    async def update_model(
            self,
            model_id: str,
            updates: LLMModel,
            scope: Optional[OwnerScope] = None,
            *,
            allow_global_mutation: bool = False,
    ) -> LLMModel:
        async with self._uow_factory() as uow:
            existing = await uow.llm_model.get_by_id(model_id, scope=scope)
            if not existing:
                raise NotFoundError(f"模型[{model_id}]不存在")
            if (
                updates.visibility == ResourceVisibility.GLOBAL
                and not allow_global_mutation
            ):
                raise ForbiddenError("只有管理员可修改全局模型")
            if existing.visibility != updates.visibility:
                raise BadRequestError("模型可见性不可通过更新修改，请新建模型")
            if existing.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
                raise ForbiddenError("只有管理员可修改全局模型")
            endpoint_id = updates.endpoint_id.strip() or existing.endpoint_id
            endpoint = await uow.llm_endpoint.get_by_id(endpoint_id, scope=scope)
            if not endpoint:
                raise BadRequestError(f"端点[{endpoint_id}]不存在或不可访问")
            updates.id = model_id
            updates.endpoint_id = endpoint_id
            updates.provider = endpoint.provider
            updates.base_url = endpoint.base_url
            updates.api_key = endpoint.api_key
            self._bind_ownership(updates, scope)
            self._validate_model(updates)
            if updates.is_default != existing.is_default:
                raise BadRequestError("请使用专用接口修改系统默认模型")
            updates.is_default = False
            await uow.llm_model.save(updates)
        return self._mask(updates)

    async def delete_model(
            self,
            model_id: str,
            scope: Optional[OwnerScope] = None,
            *,
            allow_global_mutation: bool = False,
    ) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.llm_model.get_by_id(model_id, scope=scope)
            if not existing:
                raise NotFoundError(f"模型[{model_id}]不存在")
            if existing.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
                raise ForbiddenError("只有管理员可删除全局模型")
            if existing.visibility == ResourceVisibility.GLOBAL:
                global_count = await uow.llm_model.count_global()
                if global_count <= 1:
                    raise BadRequestError("至少保留一个全局模型配置")
            else:
                count = await uow.llm_model.count()
                if count <= 1:
                    raise BadRequestError("至少保留一个模型配置")
            was_system_default = (
                await uow.llm_model_preference.get_model_id(None)
            ) == existing.id
            await uow.llm_model.delete_by_id(model_id)
            if was_system_default:
                models = await uow.llm_model.get_all_global()
                if models:
                    await uow.llm_model_preference.set_model_id(
                        None,
                        models[0].id,
                    )

    async def set_default(self, model_id: str) -> LLMModel:
        async with self._uow_factory() as uow:
            model = await uow.llm_model.get_by_id(model_id)
            if not model:
                raise NotFoundError(f"模型[{model_id}]不存在")
            if model.visibility != ResourceVisibility.GLOBAL:
                raise BadRequestError("只有全局模型可设为系统默认")
            self._validate_model(model)
            await uow.llm_model_preference.set_model_id(None, model.id)
        return self._mask(self._project_preference(model, model.id))

    async def set_preference(
            self,
            model_id: str,
            *,
            scope: OwnerScope,
    ) -> LLMModel:
        async with self._uow_factory() as uow:
            model = await uow.llm_model.get_by_id(model_id, scope=scope)
            if not model:
                raise NotFoundError(f"模型[{model_id}]不存在或无权访问")
            self._ensure_invokable(model)
            await uow.llm_model_preference.set_model_id(scope, model.id)
        return self._mask(self._project_preference(model, model.id))

    async def probe_multimodal(
            self,
            model_id: str,
            *,
            scope: OwnerScope,
            allow_global_mutation: bool = False,
    ) -> dict:
        model = await self.get_model(model_id, mask=False, scope=scope)
        if model.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
            raise ForbiddenError("只有管理员可探测并修改全局模型能力")
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
            await uow.llm_model.save(model)
        return probe

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
