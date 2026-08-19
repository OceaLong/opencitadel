#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Optional, Dict

from fastapi import APIRouter, Depends

from app.domain.errors import BadRequestError, ForbiddenError
from app.application.services.llm_model_service import LLMModelService
from app.application.services.audit_service import AuditService
from app.domain.models.llm_model import LLMModel, ResourceVisibility
from app.domain.models.scope import WorkspaceContext
from app.domain.models.scope import OwnerScopeType
from app.domain.models.team import TeamRole
from app.interfaces.auth_dependencies import (
    get_workspace_context,
    require_admin,
    require_non_auditor,
)
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.llm_model import (
    LLMModelCreateRequest,
    LLMModelUpdateRequest,
    LLMModelResponse,
    LLMModelListResponse,
    MultimodalProbeResponse,
)
from app.interfaces.service_dependencies import get_audit_service, get_llm_model_service
from app.interfaces.workspace_audit import record_workspace_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-models", tags=["模型管理"])


def _to_response(model: LLMModel) -> LLMModelResponse:
    return LLMModelResponse(
        id=model.id,
        endpoint_id=model.endpoint_id,
        display_name=model.display_name,
        provider=model.provider.value if hasattr(model.provider, "value") else model.provider,
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
        team_id=model.team_id,
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
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[LLMModelResponse]:
    if request.is_default:
        raise BadRequestError("请使用专用接口修改系统默认模型")
    model = LLMModel(**request.model_dump())
    if not ctx.principal.is_admin:
        model.visibility = ResourceVisibility.PRIVATE
        model.owner_user_id = ctx.principal.user_id
    created = await llm_model_service.create_model(
        model,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    await record_workspace_audit(
        audit_service,
        ctx,
        action="llm_model_create",
        resource_type="llm_model",
        resource_id=created.id,
        metadata={
            "after": {
                "endpoint_id": created.endpoint_id,
                "provider": created.provider.value,
                "model_name": created.model_name,
                "visibility": created.visibility.value,
                "team_id": created.team_id,
            }
        },
    )
    return Response.success( data=_to_response(created))


@router.put("/{model_id}", response_model=Response[LLMModelResponse])
async def update_model(
        model_id: str,
        request: LLMModelUpdateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[LLMModelResponse]:
    if request.is_default is not None:
        raise BadRequestError("请使用专用接口修改系统默认模型")
    existing = await llm_model_service.get_model(model_id, mask=False, scope=ctx.scope)
    if existing.visibility == ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局模型仅管理员可修改")
    data = existing.model_dump()
    for k, v in request.model_dump(exclude_unset=True).items():
        if v is not None:
            data[k] = v
    updated = LLMModel(**data)
    result = await llm_model_service.update_model(
        model_id,
        updated,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    await record_workspace_audit(
        audit_service,
        ctx,
        action="llm_model_update",
        resource_type="llm_model",
        resource_id=result.id,
        metadata={
            "before": {
                "endpoint_id": existing.endpoint_id,
                "model_name": existing.model_name,
                "visibility": existing.visibility.value,
            },
            "after": {
                "endpoint_id": result.endpoint_id,
                "model_name": result.model_name,
                "visibility": result.visibility.value,
            },
        },
    )
    return Response.success( data=_to_response(result))


@router.delete("/{model_id}", response_model=Response[Optional[Dict]])
async def delete_model(
        model_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[Optional[Dict]]:
    existing = await llm_model_service.get_model(model_id, mask=False, scope=ctx.scope)
    if existing.visibility == ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局模型仅管理员可删除")
    await llm_model_service.delete_model(
        model_id,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    await record_workspace_audit(
        audit_service,
        ctx,
        action="llm_model_delete",
        resource_type="llm_model",
        resource_id=model_id,
        metadata={
            "before": {
                "endpoint_id": existing.endpoint_id,
                "model_name": existing.model_name,
                "visibility": existing.visibility.value,
            }
        },
    )
    return Response.success()


@router.post("/{model_id}/set-default", response_model=Response[LLMModelResponse])
async def set_default_model(
        model_id: str,
        _admin=Depends(require_admin),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[LLMModelResponse]:
    model = await llm_model_service.get_model(model_id, mask=False)
    if model.visibility != ResourceVisibility.GLOBAL:
        raise BadRequestError("只有全局模型可设为系统默认")
    model = await llm_model_service.set_default(model.id)
    await record_workspace_audit(
        audit_service,
        ctx,
        action="llm_system_default_set",
        resource_type="llm_model",
        resource_id=model.id,
        metadata={"decision": "set_system_default"},
    )
    return Response.success( data=_to_response(model))


@router.post("/{model_id}/set-preferred", response_model=Response[LLMModelResponse])
async def set_preferred_model(
        model_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard=Depends(require_non_auditor),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[LLMModelResponse]:
    if ctx.scope.type == OwnerScopeType.TEAM:
        role = ctx.principal.team_roles.get(ctx.scope.team_id or "")
        if role not in {TeamRole.OWNER, TeamRole.ADMIN}:
            raise ForbiddenError("只有团队所有者或管理员可修改团队默认模型")
    model = await llm_model_service.set_preference(model_id, scope=ctx.scope)
    await record_workspace_audit(
        audit_service,
        ctx,
        action="llm_workspace_preference_set",
        resource_type="llm_model",
        resource_id=model.id,
        metadata={"decision": "set_workspace_preference"},
    )
    return Response.success(data=_to_response(model))


@router.post("/{model_id}/probe-multimodal", response_model=Response[MultimodalProbeResponse])
async def probe_multimodal(
        model_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[MultimodalProbeResponse]:
    await llm_model_service.get_model(model_id, scope=ctx.scope)
    result = await llm_model_service.probe_multimodal(
        model_id,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    await record_workspace_audit(
        audit_service,
        ctx,
        action="llm_model_probe",
        resource_type="llm_model",
        resource_id=model_id,
        metadata={
            "decision": result.get("status"),
            "error_code": result.get("error_code"),
        },
    )
    return Response.success(data=MultimodalProbeResponse(**result),
    )
