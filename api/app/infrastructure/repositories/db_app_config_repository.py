#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete

from app.application.errors.exceptions import NotFoundError, ServerRequestsError
from app.domain.models.app_config import AppConfig
from app.domain.models.app_config_revision import AppConfigRevision
from app.domain.models.app_config_scope import (
    GLOBAL_CONFIG_ID,
    AppConfigScope,
    USER_OVERRIDABLE_SECTIONS,
    user_config_id,
)
from app.domain.repositories.app_config_repository import AppConfigRepository
from app.infrastructure.models.app_config import AppConfigModel, AppConfigRevisionModel
from app.infrastructure.storage.postgres import get_postgres

logger = logging.getLogger(__name__)


class DbAppConfigRepository(AppConfigRepository):
    """基于 PostgreSQL 的应用配置仓库（支持 global + user 覆盖 + 版本历史）"""

    async def load_global(self) -> Optional[AppConfig]:
        return await self._load_by_id(GLOBAL_CONFIG_ID)

    async def load_user_override(self, user_id: str) -> Optional[AppConfig]:
        payload = await self.load_user_override_payload(user_id)
        if not payload:
            return None
        return AppConfig.model_validate(payload)

    async def load_user_override_payload(self, user_id: str) -> Dict[str, Any]:
        try:
            async with get_postgres().session_factory() as session:
                result = await session.execute(
                    select(AppConfigModel).where(AppConfigModel.id == user_config_id(user_id))
                )
                record = result.scalar_one_or_none()
                if record is None or not record.payload:
                    return {}
                return dict(record.payload)
        except Exception as exc:
            logger.error("读取用户配置覆盖失败: %s", exc)
            raise ServerRequestsError("读取用户配置覆盖失败，请稍后尝试") from exc

    async def load(self) -> Optional[AppConfig]:
        return await self.load_global()

    async def save(self, app_config: AppConfig) -> None:
        await self.save_global(app_config)

    async def save_global(
        self,
        app_config: AppConfig,
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        await self._save_record(
            config_id=GLOBAL_CONFIG_ID,
            scope=AppConfigScope.GLOBAL.value,
            owner_user_id=None,
            payload=app_config.model_dump(mode="json"),
            changed_by=changed_by,
            note=note,
        )

    async def save_user_override(
        self,
        user_id: str,
        partial_config: AppConfig,
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        from app.application.services.owner_config_resolver import validate_user_override_payload

        payload = partial_config.model_dump(mode="json")
        filtered = {key: payload[key] for key in USER_OVERRIDABLE_SECTIONS if key in payload}
        validate_user_override_payload(filtered)
        await self.save_user_override_payload(
            user_id,
            filtered,
            changed_by=changed_by,
            note=note,
        )

    async def save_user_override_payload(
        self,
        user_id: str,
        payload: Dict[str, Any],
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        await self._save_record(
            config_id=user_config_id(user_id),
            scope=AppConfigScope.USER.value,
            owner_user_id=user_id,
            payload=payload,
            changed_by=changed_by,
            note=note,
        )

    async def delete_user_override(self, user_id: str) -> None:
        try:
            async with get_postgres().session_factory() as session:
                await session.execute(
                    delete(AppConfigModel).where(AppConfigModel.id == user_config_id(user_id))
                )
                await session.commit()
        except Exception as exc:
            logger.error("删除用户配置覆盖失败: %s", exc)
            raise ServerRequestsError("删除用户配置覆盖失败，请稍后尝试") from exc

    async def list_revisions(
        self,
        *,
        config_id: Optional[str] = None,
        scope: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AppConfigRevision]:
        if not config_id and not scope and owner_user_id is None:
            raise ServerRequestsError("配置版本查询必须指定 config_id、scope 或 owner_user_id 之一")
        try:
            async with get_postgres().session_factory() as session:
                stmt = select(AppConfigRevisionModel).order_by(AppConfigRevisionModel.created_at.desc())
                if config_id:
                    stmt = stmt.where(AppConfigRevisionModel.config_id == config_id)
                if scope:
                    stmt = stmt.where(AppConfigRevisionModel.scope == scope)
                if owner_user_id is not None:
                    stmt = stmt.where(AppConfigRevisionModel.owner_user_id == owner_user_id)
                stmt = stmt.limit(limit).offset(offset)
                result = await session.execute(stmt)
                return [self._revision_to_domain(r) for r in result.scalars().all()]
        except Exception as exc:
            logger.error("读取配置版本历史失败: %s", exc)
            raise ServerRequestsError("读取配置版本历史失败，请稍后尝试") from exc

    async def get_revision(self, revision_id: str) -> Optional[AppConfigRevision]:
        try:
            async with get_postgres().session_factory() as session:
                result = await session.execute(
                    select(AppConfigRevisionModel).where(AppConfigRevisionModel.id == revision_id)
                )
                record = result.scalar_one_or_none()
                return self._revision_to_domain(record) if record else None
        except Exception as exc:
            logger.error("读取配置版本失败: %s", exc)
            raise ServerRequestsError("读取配置版本失败，请稍后尝试") from exc

    async def rollback_to_revision(
        self,
        revision_id: str,
        *,
        changed_by: Optional[str] = None,
    ) -> AppConfig:
        revision = await self.get_revision(revision_id)
        if revision is None:
            raise NotFoundError(f"配置版本[{revision_id}]不存在")
        restored = AppConfig.model_validate(revision.payload)
        await self._save_record(
            config_id=revision.config_id,
            scope=revision.scope,
            owner_user_id=revision.owner_user_id,
            payload=revision.payload,
            changed_by=changed_by,
            note=f"rollback:{revision_id}",
        )
        return restored

    async def _load_by_id(self, config_id: str) -> Optional[AppConfig]:
        try:
            async with get_postgres().session_factory() as session:
                result = await session.execute(
                    select(AppConfigModel).where(AppConfigModel.id == config_id)
                )
                record = result.scalar_one_or_none()
                if record is None or not record.payload:
                    return None
                return AppConfig.model_validate(record.payload)
        except Exception as exc:
            logger.error("读取 DB 应用配置失败: %s", exc)
            raise ServerRequestsError("读取应用配置失败，请稍后尝试") from exc

    async def _save_record(
        self,
        *,
        config_id: str,
        scope: str,
        owner_user_id: Optional[str],
        payload: Dict[str, Any],
        changed_by: Optional[str],
        note: str,
    ) -> None:
        try:
            async with get_postgres().session_factory() as session:
                result = await session.execute(
                    select(AppConfigModel).where(AppConfigModel.id == config_id)
                )
                record = result.scalar_one_or_none()
                if record is not None and record.payload:
                    session.add(
                        AppConfigRevisionModel(
                            id=str(uuid.uuid4()),
                            config_id=config_id,
                            scope=record.scope,
                            owner_user_id=record.owner_user_id,
                            payload=record.payload,
                            changed_by=changed_by,
                            note=note or "update",
                        )
                    )
                if record is None:
                    session.add(
                        AppConfigModel(
                            id=config_id,
                            scope=scope,
                            owner_user_id=owner_user_id,
                            payload=payload,
                        )
                    )
                else:
                    record.payload = payload
                    record.scope = scope
                    record.owner_user_id = owner_user_id
                    record.updated_at = datetime.utcnow()
                await session.commit()
        except Exception as exc:
            logger.error("写入 DB 应用配置失败: %s", exc)
            raise ServerRequestsError("写入应用配置失败，请稍后尝试") from exc

    @staticmethod
    def _revision_to_domain(record: AppConfigRevisionModel) -> AppConfigRevision:
        return AppConfigRevision(
            id=record.id,
            config_id=record.config_id,
            scope=record.scope,
            owner_user_id=record.owner_user_id,
            payload=record.payload or {},
            changed_by=record.changed_by,
            note=record.note or "",
            created_at=record.created_at,
        )
