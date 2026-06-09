#!/usr/bin/env python
# -*- coding: utf-8 -*-
from fastapi import APIRouter

from . import (
    status_routes,
    app_config_routes,
    file_routes,
    session_routes,
    llm_model_routes,
    skill_routes,
    memory_routes,
    metrics_routes,
    marketplace_routes,
    questionnaire_routes,
    room_routes,
    codebase_routes,
)


def create_api_routes() -> APIRouter:
    """创建API路由，涵盖整个项目的所有路由管理"""
    # 1.创建APIRouter实例
    api_router = APIRouter()

    # 2.将各个模块添加到api_router中
    api_router.include_router(status_routes.router)
    api_router.include_router(app_config_routes.router)
    api_router.include_router(file_routes.router)
    api_router.include_router(session_routes.router)
    api_router.include_router(llm_model_routes.router)
    api_router.include_router(skill_routes.router)
    api_router.include_router(memory_routes.memory_router)
    api_router.include_router(metrics_routes.router)
    api_router.include_router(marketplace_routes.router)
    api_router.include_router(questionnaire_routes.router)
    api_router.include_router(room_routes.router)
    api_router.include_router(codebase_routes.router)

    # 3.返回api路由实例
    return api_router


router = create_api_routes()
