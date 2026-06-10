#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.application.errors.exceptions import ServerRequestsError
from app.domain.models.app_config import AppConfig
from app.domain.repositories.app_config_repository import AppConfigRepository
from app.infrastructure.models.app_config import AppConfigModel
from app.infrastructure.storage.postgres import get_postgres

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_ID = "default"


class DbAppConfigRepository(AppConfigRepository):
    """基于 PostgreSQL 的应用配置仓库（支持多副本共享）"""

    async def load(self) -> Optional[AppConfig]:
        try:
            async with get_postgres().session_factory() as session:
                result = await session.execute(
                    select(AppConfigModel).where(AppConfigModel.id == _DEFAULT_CONFIG_ID)
                )
                record = result.scalar_one_or_none()
                if record is None or not record.payload:
                    return AppConfig()
                return AppConfig.model_validate(record.payload)
        except Exception as exc:
            logger.error("读取 DB 应用配置失败: %s", exc)
            raise ServerRequestsError("读取应用配置失败，请稍后尝试") from exc

    async def save(self, app_config: AppConfig) -> None:
        payload = app_config.model_dump(mode="json")
        try:
            async with get_postgres().session_factory() as session:
                result = await session.execute(
                    select(AppConfigModel).where(AppConfigModel.id == _DEFAULT_CONFIG_ID)
                )
                record = result.scalar_one_or_none()
                if record is None:
                    session.add(AppConfigModel(id=_DEFAULT_CONFIG_ID, payload=payload))
                else:
                    record.payload = payload
                    record.updated_at = datetime.utcnow()
                await session.commit()
        except Exception as exc:
            logger.error("写入 DB 应用配置失败: %s", exc)
            raise ServerRequestsError("写入应用配置失败，请稍后尝试") from exc
