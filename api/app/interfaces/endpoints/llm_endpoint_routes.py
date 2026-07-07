#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Optional, Dict

from fastapi import APIRouter, Depends

from app.application.errors.exceptions import ForbiddenError
from app.application.services.llm_endpoint_service import LLMEndpointService
from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.llm_model import ResourceVisibility
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.llm_endpoint import (
    LLMEndpointCreateRequest,
    LLMEndpointUpdateRequest,
    LLMEndpointResponse,
    LLMEndpointListResponse,
    LLMEndpointModelSummary,
)
from app.interfaces.service_dependencies import get_llm_endpoint_service, get_llm_model_service
from app.application.services.llm_model_service import LLMModelService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-endpoints", tags=["LLM 端点管理"])


async def _to_response(
        endpoint: LLMEndpoint,
        llm_endpoint_service: LLMEndpointService,
        llm_model_service: LLMModelService,
        *,
        scope,
        include_models: bool = False,
) -> LLMEndpointResponse:
    model_count = await llm_endpoint_service.count_models(endpoint.id)
    models = []
    if include_models:
        model_rows = await llm_model_service.list_models(mask=True, scope=scope)
        models = [
            LLMEndpointModelSummary(
                id=m.id,
                display_name=m.display_name,
                model_name=m.model_name,
                is_default=m.is_default,
            )
            for m in model_rows
            if m.endpoint_id == endpoint.id
        ]
    return LLMEndpointResponse(
        id=endpoint.id,
        display_name=endpoint.display_name,
        provider=endpoint.provider,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        visibility=endpoint.visibility.value if hasattr(endpoint.visibility, "value") else endpoint.visibility,
        owner_user_id=endpoint.owner_user_id,
        model_count=model_count,
        models=models,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


@router.get("", response_model=Response[LLMEndpointListResponse])
async def list_endpoints(
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_endpoint_service: LLMEndpointService = Depends(get_llm_endpoint_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMEndpointListResponse]:
    endpoints = await llm_endpoint_service.list_endpoints(scope=ctx.scope)
    items = [
        await _to_response(
            endpoint,
            llm_endpoint_service,
            llm_model_service,
            scope=ctx.scope,
        )
        for endpoint in endpoints
    ]
    return Response.success(data=LLMEndpointListResponse(endpoints=items))


@router.get("/{endpoint_id}", response_model=Response[LLMEndpointResponse])
async def get_endpoint(
        endpoint_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_endpoint_service: LLMEndpointService = Depends(get_llm_endpoint_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMEndpointResponse]:
    endpoint = await llm_endpoint_service.get_endpoint(endpoint_id, scope=ctx.scope)
    return Response.success(data=await _to_response(
            endpoint,
            llm_endpoint_service,
            llm_model_service,
            scope=ctx.scope,
            include_models=True,
        )
    )


@router.post("", response_model=Response[LLMEndpointResponse])
async def create_endpoint(
        request: LLMEndpointCreateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_endpoint_service: LLMEndpointService = Depends(get_llm_endpoint_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMEndpointResponse]:
    endpoint = LLMEndpoint(**request.model_dump())
    if not ctx.principal.is_admin:
        endpoint.visibility = ResourceVisibility.PRIVATE
        endpoint.owner_user_id = ctx.principal.user_id
    created = await llm_endpoint_service.create_endpoint(endpoint, scope=ctx.scope)
    return Response.success(data=await _to_response(
            created,
            llm_endpoint_service,
            llm_model_service,
            scope=ctx.scope,
        ),
    )


@router.put("/{endpoint_id}", response_model=Response[LLMEndpointResponse])
async def update_endpoint(
        endpoint_id: str,
        request: LLMEndpointUpdateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_endpoint_service: LLMEndpointService = Depends(get_llm_endpoint_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
) -> Response[LLMEndpointResponse]:
    existing = await llm_endpoint_service.get_endpoint(endpoint_id, mask=False, scope=ctx.scope)
    if existing.visibility == ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局端点仅管理员可修改")
    data = existing.model_dump()
    for key, value in request.model_dump(exclude_unset=True).items():
        if value is not None:
            data[key] = value
    updated = LLMEndpoint(**data)
    result = await llm_endpoint_service.update_endpoint(endpoint_id, updated, scope=ctx.scope)
    return Response.success(data=await _to_response(
            result,
            llm_endpoint_service,
            llm_model_service,
            scope=ctx.scope,
        ),
    )


@router.delete("/{endpoint_id}", response_model=Response[Optional[Dict]])
async def delete_endpoint(
        endpoint_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_endpoint_service: LLMEndpointService = Depends(get_llm_endpoint_service),
) -> Response[Optional[Dict]]:
    existing = await llm_endpoint_service.get_endpoint(endpoint_id, mask=False, scope=ctx.scope)
    if existing.visibility == ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局端点仅管理员可删除")
    await llm_endpoint_service.delete_endpoint(endpoint_id, scope=ctx.scope)
    return Response.success()
