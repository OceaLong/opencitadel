#!/usr/bin/env python
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends

from app.interfaces.auth_dependencies import get_current_principal

from . import (
    auth_routes,
    admin_routes,
    status_routes,
    llm_status_routes,
    app_config_routes,
    file_routes,
    service_api_key_routes,
    team_routes,
    session_routes,
    llm_model_routes,
    skill_routes,
    memory_routes,
    metrics_routes,
    marketplace_routes,
    codebase_routes,
    knowledge_base_routes,
    artifact_routes,
    scheduling_routes,
)


def create_api_routes() -> APIRouter:
    """创建API路由，涵盖整个项目的所有路由管理"""
    # 1.创建APIRouter实例
    api_router = APIRouter()
    authenticated_router = APIRouter(dependencies=[Depends(get_current_principal)])

    # 2.公开模块：认证引导、健康检查、公开分享等
    api_router.include_router(auth_routes.router)
    api_router.include_router(status_routes.router)
    api_router.include_router(llm_status_routes.router)
    api_router.include_router(metrics_routes.router)
    api_router.include_router(marketplace_routes.router)

    # 3.默认鉴权模块：新增业务接口若无明确公开需求，应放在这里。
    authenticated_router.include_router(admin_routes.router)
    authenticated_router.include_router(team_routes.router)
    authenticated_router.include_router(team_routes.invitation_router)
    authenticated_router.include_router(service_api_key_routes.router)
    authenticated_router.include_router(app_config_routes.router)
    authenticated_router.include_router(file_routes.router)
    authenticated_router.include_router(session_routes.router)
    authenticated_router.include_router(llm_model_routes.router)
    authenticated_router.include_router(skill_routes.router)
    authenticated_router.include_router(memory_routes.memory_router)
    authenticated_router.include_router(codebase_routes.router)
    authenticated_router.include_router(knowledge_base_routes.router)
    authenticated_router.include_router(artifact_routes.router)
    authenticated_router.include_router(scheduling_routes.scheduled_router)
    authenticated_router.include_router(scheduling_routes.notification_router)
    api_router.include_router(scheduling_routes.webhook_router)
    api_router.include_router(artifact_routes.share_router)
    api_router.include_router(authenticated_router)

    # 4.返回api路由实例
    return api_router


router = create_api_routes()
