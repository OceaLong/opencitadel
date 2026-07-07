#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Optional, Dict

from fastapi import APIRouter, Depends, Query

from app.application.errors.exceptions import ForbiddenError
from app.application.services.llm_model_service import LLMModelService
from app.application.services.skill_recommender_service import SkillRecommenderService
from app.application.services.skill_service import SkillService
from app.container import ApiContainer
from app.domain.external.json_parser import JSONParser
from app.domain.models.scope import WorkspaceContext
from app.domain.models.skill import ResourceVisibility, Skill
from app.infrastructure.external.llm.resilient_llm import create_resilient_llm
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.skill import (
    SkillCreateRequest,
    SkillUpdateRequest,
    SkillResponse,
    SkillListResponse,
    SkillRecommendResponse,
    SkillImportRequest,
)
from app.interfaces.service_dependencies import get_skill_service, get_llm_model_service
from dependency_injector.wiring import Provide, inject

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skills", tags=["Skill管理"])


def _to_response(skill: Skill) -> SkillResponse:
    return SkillResponse(**skill.model_dump())


@router.get("", response_model=Response[SkillListResponse])
async def list_skills(
        enabled_only: bool = Query(default=False),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        skill_service: SkillService = Depends(get_skill_service),
) -> Response[SkillListResponse]:
    skills = await skill_service.list_skills(enabled_only=enabled_only, scope=ctx.scope)
    return Response.success(data=SkillListResponse(skills=[_to_response(s) for s in skills]))


@router.get("/recommend", response_model=Response[SkillRecommendResponse])
@inject
async def recommend_skill(
        message: str = Query(..., min_length=1),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        skill_service: SkillService = Depends(get_skill_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        json_parser: JSONParser = Depends(Provide[ApiContainer.json_parser]),
) -> Response[SkillRecommendResponse]:
    skills = await skill_service.list_skills(enabled_only=True, scope=ctx.scope)
    llm_model = await llm_model_service.get_default_model()
    if llm_model is None:
        return Response.success(data=SkillRecommendResponse())
    llm = create_resilient_llm(llm_model, llm_model_service=llm_model_service)
    recommender = SkillRecommenderService(llm, json_parser)
    result = await recommender.recommend(message, skills)
    return Response.success(data=SkillRecommendResponse(**result.model_dump()))


@router.post("/import", response_model=Response[SkillResponse])
async def import_skill(
        request: SkillImportRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        skill_service: SkillService = Depends(get_skill_service),
) -> Response[SkillResponse]:
    created = await skill_service.import_from_markdown(
        request.content,
        slug=request.slug,
        scope=ctx.scope,
    )
    return Response.success( data=_to_response(created))


@router.get("/{skill_id}", response_model=Response[SkillResponse])
async def get_skill(
        skill_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        skill_service: SkillService = Depends(get_skill_service),
) -> Response[SkillResponse]:
    skill = await skill_service.get_skill(skill_id, scope=ctx.scope)
    return Response.success(data=_to_response(skill))


@router.post("", response_model=Response[SkillResponse])
async def create_skill(
        request: SkillCreateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        skill_service: SkillService = Depends(get_skill_service),
) -> Response[SkillResponse]:
    skill = Skill(**request.model_dump())
    if not ctx.principal.is_admin:
        skill.visibility = ResourceVisibility.PRIVATE
        skill.owner_user_id = ctx.principal.user_id
    created = await skill_service.create_skill(skill, scope=ctx.scope)
    return Response.success( data=_to_response(created))


@router.put("/{skill_id}", response_model=Response[SkillResponse])
async def update_skill(
        skill_id: str,
        request: SkillUpdateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        skill_service: SkillService = Depends(get_skill_service),
) -> Response[SkillResponse]:
    existing = await skill_service.get_skill(skill_id, scope=ctx.scope)
    if existing.visibility == ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局 Skill 仅管理员可修改")
    data = existing.model_dump()
    for k, v in request.model_dump(exclude_unset=True).items():
        if v is not None:
            data[k] = v
    updated = Skill(**data)
    result = await skill_service.update_skill(skill_id, updated, scope=ctx.scope)
    return Response.success( data=_to_response(result))


@router.delete("/{skill_id}", response_model=Response[Optional[Dict]])
async def delete_skill(
        skill_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        skill_service: SkillService = Depends(get_skill_service),
) -> Response[Optional[Dict]]:
    existing = await skill_service.get_skill(skill_id, scope=ctx.scope)
    if existing.visibility == ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局 Skill 仅管理员可删除")
    await skill_service.delete_skill(skill_id, scope=ctx.scope)
    return Response.success()
