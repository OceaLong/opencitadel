#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed app_configs from config.yaml when DB row is empty (cold start migration)."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from sqlalchemy import select

from app.domain.models.app_config import AppConfig
from app.domain.models.app_config_scope import GLOBAL_CONFIG_ID, LEGACY_DEFAULT_CONFIG_ID
from app.infrastructure.models.app_config import AppConfigModel
from app.infrastructure.storage.postgres import get_postgres
from core.config import get_settings

logger = logging.getLogger(__name__)


def _load_yaml_config(filepath: str) -> AppConfig:
    path = Path(filepath)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        logger.warning("config.yaml 不存在，使用 AppConfig 默认值: %s", path)
        return AppConfig()
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(data)


async def seed_app_config_from_yaml_if_empty() -> bool:
    """Idempotent: write config.yaml into DB when global row payload is empty."""
    settings = get_settings()
    postgres = get_postgres()
    await postgres.init()
    try:
        async with postgres.session_factory() as session:
            result = await session.execute(
                select(AppConfigModel).where(
                    AppConfigModel.id.in_([GLOBAL_CONFIG_ID, LEGACY_DEFAULT_CONFIG_ID])
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                logger.info("app_configs 无 global 行，跳过种子（应由 Alembic 创建）")
                return False
            if record.id == LEGACY_DEFAULT_CONFIG_ID:
                record.id = GLOBAL_CONFIG_ID
                record.scope = "global"
            payload = record.payload or {}
            if payload and payload != {}:
                logger.info("app_configs 已有配置，跳过 YAML 种子")
                return False

            app_config = _load_yaml_config(settings.app_config_filepath)
            record.payload = app_config.model_dump(mode="json")
            record.scope = "global"
            await session.commit()
            logger.info("已将 config.yaml 种子写入 app_configs.global")
            return True
    finally:
        await postgres.shutdown()
