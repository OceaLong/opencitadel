#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Optional, Dict

from fastapi import APIRouter, Depends, Query

from app.application.errors.exceptions import ForbiddenError
from app.application.services.skill_service import SkillService
from app.domain.models.scope import WorkspaceContext
from app.domain.models.skill import ResourceVisibility, Skill
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.skill import (
    SkillCreateRequest,
    SkillUpdateRequest,
    SkillResponse,
    SkillListResponse,
)
from app.interfaces.service_dependencies import get_skill_service

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
    return Response.success(msg="创建Skill成功", data=_to_response(created))


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
    return Response.success(msg="更新Skill成功", data=_to_response(result))


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
    return Response.success(msg="删除Skill成功")
