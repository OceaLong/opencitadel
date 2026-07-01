#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable

from app.application.services.skill_service import SkillService
from app.domain.models.user import GlobalRole, User
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.security.password_hasher import PasswordHasher
from core.config import get_settings

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

    try:
        await bootstrap_admin_user(uow_factory)
    except Exception as e:
        logger.warning(f"启动管理员种子化失败(可能数据库未迁移): {e}")


def _needs_password_backfill(user: User) -> bool:
    return not user.password_hash or not user.password_hash.strip()


async def bootstrap_admin_user(uow_factory: Callable[[], IUnitOfWork]) -> None:
    settings = get_settings()
    email = (settings.bootstrap_admin_email or "").strip().lower()
    if not email:
        return
    async with uow_factory() as uow:
        existing = await uow.user.get_by_email(email)
        if existing:
            if _needs_password_backfill(existing) and settings.bootstrap_admin_password:
                existing.password_hash = PasswordHasher().hash(settings.bootstrap_admin_password)
                await uow.user.save(existing)
                logger.info("Bootstrap admin password backfilled: %s", email)
            return

        users = await uow.user.list(limit=1)
        if users:
            return

        password_hash = ""
        if settings.bootstrap_admin_password:
            password_hash = PasswordHasher().hash(settings.bootstrap_admin_password)
        user = User(
            email=email,
            username=email.split("@", 1)[0] or "admin",
            password_hash=password_hash or None,
            display_name="Administrator",
            global_role=GlobalRole.ADMIN,
        )
        await uow.user.save(user)
        logger.info("Bootstrap admin user created: %s", email)
