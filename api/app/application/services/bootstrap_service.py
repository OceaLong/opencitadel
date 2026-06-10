#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable

from app.application.services.skill_service import SkillService
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


async def bootstrap_data(
        uow_factory: Callable[[], IUnitOfWork],
        skill_service: SkillService,
) -> None:
    """启动时种子化内置 Skill（LLM 模型通过设置页在库表中配置）。"""
    try:
        await skill_service.seed_builtin_skills()
    except Exception as e:
        logger.warning(f"启动种子化失败(可能数据库未就绪): {e}")
