#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable

from app.application.services.llm_model_service import LLMModelService
from app.application.services.skill_service import SkillService
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.repositories.file_app_config_repository import FileAppConfigRepository
from core.config import get_settings

logger = logging.getLogger(__name__)


async def bootstrap_data(
        uow_factory: Callable[[], IUnitOfWork],
        llm_model_service: LLMModelService,
        skill_service: SkillService,
) -> None:
    """启动时种子化默认模型与内置Skill"""
    settings = get_settings()
    try:
        app_config = FileAppConfigRepository(settings.app_config_filepath).load()
        async with uow_factory() as uow:
            count = await uow.llm_model.count()
        if count == 0:
            await llm_model_service.sync_from_llm_config(app_config.llm_config)
            logger.info("已从config.yaml种子化默认LLM模型")
        await skill_service.seed_builtin_skills()
    except Exception as e:
        logger.warning(f"启动种子化失败(可能数据库未就绪): {e}")
