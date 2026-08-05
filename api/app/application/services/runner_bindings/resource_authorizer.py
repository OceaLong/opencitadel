#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Codebase / knowledge-base session-resource authorization.

Moved out of task_runner_factory.py verbatim (phase-4 engineering-quality
Task 3) — behavior-preserving extraction, not a rewrite.
TaskRunnerFactory._authorize_session_resources remains as a thin delegate
(several tests call it as a bound method on TaskRunnerFactory directly).
"""
from typing import Callable, Optional

from app.application.errors.exceptions import NotFoundError
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
        if session.codebase_id:
            codebase = await uow.codebase.get_by_id(session.codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError("会话关联代码库不存在或无权访问")
            bindings = [
                binding
                for binding in session.resource_bindings
                if binding.resource_kind == ResourceKind.CODEBASE
            ]
            if (
                len(bindings) != 1
                or bindings[0].resource_id != session.codebase_id
            ):
                raise NotFoundError(
                    "会话代码库版本绑定缺失、重复或不匹配"
                )
            binding = bindings[0]
            version = await uow.codebase_version.get_version(
                binding.version_id,
                codebase_id=session.codebase_id,
            )
            if (
                version is None
                or version.id != binding.version_id
                or version.codebase_id != session.codebase_id
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
        if session.knowledge_base_id:
            knowledge_base = await uow.knowledge_base.get_kb(
                session.knowledge_base_id,
                scope=scope,
            )
            if knowledge_base is None:
                raise NotFoundError("会话关联知识库不存在或无权访问")
            bindings = [
                binding
                for binding in session.resource_bindings
                if (
                    binding.resource_kind
                    == ResourceKind.KNOWLEDGE_BASE
                )
            ]
            if (
                len(bindings) != 1
                or bindings[0].resource_id
                != session.knowledge_base_id
            ):
                raise NotFoundError(
                    "会话知识库版本绑定缺失、重复或不匹配"
                )
            binding = bindings[0]
            version = await uow.knowledge_version.get_version(
                binding.version_id,
                knowledge_base_id=session.knowledge_base_id,
            )
            if (
                version is None
                or version.id != binding.version_id
                or version.knowledge_base_id
                != session.knowledge_base_id
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
