#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable

from app.application.services.llm_config_seed import is_seedable_llm_config_raw
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
        repo = FileAppConfigRepository(settings.app_config_filepath)
        raw_config = repo.load_raw()
        app_config = repo.load()
        async with uow_factory() as uow:
            count = await uow.llm_model.count()
        if count == 0 and is_seedable_llm_config_raw(raw_config) and app_config.llm_config:
            await llm_model_service.sync_from_llm_config(app_config.llm_config)
            logger.info("已从config.yaml种子化默认LLM模型")
        elif count == 0:
            logger.info("config.yaml 未提供可调用 LLM 配置，跳过默认模型种子化")
        await skill_service.seed_builtin_skills()
    except Exception as e:
        logger.warning(f"启动种子化失败(可能数据库未就绪): {e}")
