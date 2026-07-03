#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Optional, Dict

from fastapi import APIRouter, Depends

from app.application.errors.exceptions import BadRequestError, ForbiddenError
from app.application.services.llm_model_service import LLMModelService
from app.domain.models.llm_model import LLMModel, ResourceVisibility
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_admin
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.llm_model import (
    LLMModelCreateRequest,
    LLMModelUpdateRequest,
    LLMModelResponse,
    LLMModelListResponse,
    MultimodalProbeResponse,
)
from app.interfaces.service_dependencies import get_llm_model_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-models", tags=["模型管理"])


def _to_response(model: LLMModel) -> LLMModelResponse:
    return LLMModelResponse(
        id=model.id,
        display_name=model.display_name,
        provider=model.provider,
        base_url=model.base_url,
        api_key=model.api_key,
        model_name=model.model_name,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        input_price_per_million=model.input_price_per_million,
        output_price_per_million=model.output_price_per_million,
        extra_params=model.extra_params,
        capabilities=model.capabilities,
        supports_multimodal=model.supports_multimodal,
        is_default=model.is_default,
        visibility=model.visibility.value if hasattr(model.visibility, "value") else model.visibility,
        owner_user_id=model.owner_user_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


@router.get("", response_model=Response[LLMModelListResponse])
async def list_models(
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMModelListResponse]:
    models = await llm_model_service.list_models(scope=ctx.scope)
    return Response.success(data=LLMModelListResponse(models=[_to_response(m) for m in models]))


@router.get("/{model_id}", response_model=Response[LLMModelResponse])
async def get_model(
        model_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMModelResponse]:
    model = await llm_model_service.get_model(model_id, scope=ctx.scope)
    return Response.success(data=_to_response(model))


@router.post("", response_model=Response[LLMModelResponse])
async def create_model(
        request: LLMModelCreateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMModelResponse]:
    model = LLMModel(**request.model_dump())
    if not ctx.principal.is_admin:
        model.visibility = ResourceVisibility.PRIVATE
        model.owner_user_id = ctx.principal.user_id
    created = await llm_model_service.create_model(model, scope=ctx.scope)
    return Response.success(msg="创建模型成功", data=_to_response(created))


@router.put("/{model_id}", response_model=Response[LLMModelResponse])
async def update_model(
        model_id: str,
        request: LLMModelUpdateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMModelResponse]:
    existing = await llm_model_service.get_model(model_id, mask=False, scope=ctx.scope)
    if existing.visibility == ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局模型仅管理员可修改")
    data = existing.model_dump()
    for k, v in request.model_dump(exclude_unset=True).items():
        if v is not None:
            data[k] = v
    updated = LLMModel(**data)
    result = await llm_model_service.update_model(model_id, updated, scope=ctx.scope)
    return Response.success(msg="更新模型成功", data=_to_response(result))


@router.delete("/{model_id}", response_model=Response[Optional[Dict]])
async def delete_model(
        model_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[Optional[Dict]]:
    existing = await llm_model_service.get_model(model_id, mask=False, scope=ctx.scope)
    if existing.visibility == ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局模型仅管理员可删除")
    await llm_model_service.delete_model(model_id, scope=ctx.scope)
    return Response.success(msg="删除模型成功")


@router.post("/{model_id}/set-default", response_model=Response[LLMModelResponse])
async def set_default_model(
        model_id: str,
        _admin=Depends(require_admin),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMModelResponse]:
    model = await llm_model_service.get_model(model_id, mask=False)
    if model.visibility != ResourceVisibility.GLOBAL:
        raise BadRequestError("只有全局模型可设为系统默认")
    model = await llm_model_service.set_default(model.id)
    return Response.success(msg="已设为默认模型", data=_to_response(model))


@router.post("/{model_id}/probe-multimodal", response_model=Response[MultimodalProbeResponse])
async def probe_multimodal(
        model_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[MultimodalProbeResponse]:
    await llm_model_service.get_model(model_id, scope=ctx.scope)
    result = await llm_model_service.probe_multimodal(model_id)
    return Response.success(
        msg="多模态探测完成",
        data=MultimodalProbeResponse(**result),
    )
