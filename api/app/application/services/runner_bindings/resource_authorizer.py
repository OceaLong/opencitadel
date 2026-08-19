#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Codebase / knowledge-base session-resource authorization.

Moved out of task_runner_factory.py verbatim (phase-4 engineering-quality
Task 3) — behavior-preserving extraction, not a rewrite.
TaskRunnerFactory._authorize_session_resources remains as a thin delegate
(several tests call it as a bound method on TaskRunnerFactory directly).
"""
from typing import Callable, Optional

from app.domain.errors import NotFoundError
from app.domain.models.codebase import Codebase
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import KnowledgeVersionState
from app.domain.models.resource_governance import ResourceKind
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.repositories.uow import IUnitOfWork


async def authorize_session_resources(
        uow_factory: Callable[[], IUnitOfWork],
        session: Session,
        scope: OwnerScope,
) -> tuple[Optional[Codebase], Optional[str], Optional[KnowledgeBase], Optional[str]]:
    codebase = None
    codebase_version_id = None
    knowledge_base = None
    knowledge_base_version_id = None
    async with uow_factory() as uow:
        try:
            codebase_binding = session.binding_for(ResourceKind.CODEBASE)
            knowledge_binding = session.binding_for(
                ResourceKind.KNOWLEDGE_BASE
            )
        except ValueError as exc:
            raise NotFoundError("会话资源版本绑定重复") from exc
        if codebase_binding:
            codebase_id = codebase_binding.resource_id
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError("会话关联代码库不存在或无权访问")
            if codebase.id != codebase_id:
                raise NotFoundError("会话代码库版本绑定不匹配")
            version = await uow.codebase_version.get_version(
                codebase_binding.version_id,
                codebase_id=codebase_id,
            )
            if (
                version is None
                or version.id != codebase_binding.version_id
                or version.codebase_id != codebase_id
                or version.published_at is None
                or version.state not in {
                    CodebaseVersionState.READY,
                    CodebaseVersionState.DEGRADED,
                }
            ):
                raise NotFoundError(
                    "会话代码库版本不是可用的已发布版本"
                )
            codebase_version_id = version.id
        if knowledge_binding:
            knowledge_base_id = knowledge_binding.resource_id
            knowledge_base = await uow.knowledge_base.get_kb(
                knowledge_base_id,
                scope=scope,
            )
            if knowledge_base is None:
                raise NotFoundError("会话关联知识库不存在或无权访问")
            if knowledge_base.id != knowledge_base_id:
                raise NotFoundError("会话知识库版本绑定不匹配")
            version = await uow.knowledge_version.get_version(
                knowledge_binding.version_id,
                knowledge_base_id=knowledge_base_id,
            )
            if (
                version is None
                or version.id != knowledge_binding.version_id
                or version.knowledge_base_id
                != knowledge_base_id
                or version.published_at is None
                or version.state not in {
                    KnowledgeVersionState.READY,
                    KnowledgeVersionState.DEGRADED,
                }
            ):
                raise NotFoundError(
                    "会话知识库版本不是可用的已发布版本"
                )
            knowledge_base_version_id = version.id
    return (
        codebase,
        codebase_version_id,
        knowledge_base,
        knowledge_base_version_id,
    )
