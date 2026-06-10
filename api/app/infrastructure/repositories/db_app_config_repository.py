#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.application.errors.exceptions import ServerRequestsError
from app.domain.models.app_config import AppConfig
from app.domain.repositories.app_config_repository import AppConfigRepository
from app.infrastructure.models.app_config import AppConfigModel
from core.config import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_ID = "default"
_engine = None
_SessionLocal: Optional[sessionmaker] = None


def _get_sync_session() -> Session:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        sync_url = settings.sqlalchemy_database_uri.replace("+asyncpg", "")
        _engine = create_engine(sync_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _SessionLocal()


class DbAppConfigRepository(AppConfigRepository):
    """基于 PostgreSQL 的应用配置仓库（支持多副本共享）"""

    def load(self) -> Optional[AppConfig]:
        session = _get_sync_session()
        try:
            record = session.scalar(
                select(AppConfigModel).where(AppConfigModel.id == _DEFAULT_CONFIG_ID)
            )
            if record is None or not record.payload:
                return AppConfig()
            return AppConfig.model_validate(record.payload)
        except Exception as exc:
            logger.error("读取 DB 应用配置失败: %s", exc)
            raise ServerRequestsError("读取应用配置失败，请稍后尝试") from exc
        finally:
            session.close()

    def save(self, app_config: AppConfig) -> None:
        payload = app_config.model_dump(mode="json")
        session = _get_sync_session()
        try:
            record = session.scalar(
                select(AppConfigModel).where(AppConfigModel.id == _DEFAULT_CONFIG_ID)
            )
            if record is None:
                session.add(AppConfigModel(id=_DEFAULT_CONFIG_ID, payload=payload))
            else:
                record.payload = payload
                record.updated_at = datetime.utcnow()
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.error("写入 DB 应用配置失败: %s", exc)
            raise ServerRequestsError("写入应用配置失败，请稍后尝试") from exc
        finally:
            session.close()
