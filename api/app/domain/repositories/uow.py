#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import TypeVar

from .checkpoint_repository import CheckpointRepository
from .codebase_repository import CodebaseRepository
from .file_repository import FileRepository
from .knowledge_base_repository import KnowledgeBaseRepository
from .llm_model_repository import LLMModelRepository
from .llm_token_usage_repository import LLMTokenUsageRepository
from .memory_entry_repository import MemoryEntryRepository
from .audit_repository import AuditRepository
from .invitation_repository import InvitationRepository
from .oauth_identity_repository import OAuthIdentityRepository
from .quota_repository import QuotaRepository
from .refresh_token_repository import RefreshTokenRepository
from .artifact_repository import ArtifactRepository
from .scheduled_job_repository import ScheduledJobRepository
from .notification_repository import NotificationRepository
from .session_repository import SessionRepository
from .service_api_key_repository import ServiceApiKeyRepository
from .skill_repository import SkillRepository
from .team_repository import TeamRepository
from .user_repository import UserRepository

T = TypeVar("T", bound="IUnitOfWork")


class IUnitOfWork(ABC):
    """Uow模式协议接口"""
    checkpoint: CheckpointRepository
    audit: AuditRepository
    codebase: CodebaseRepository
    knowledge_base: KnowledgeBaseRepository
    file: FileRepository
    session: SessionRepository
    llm_model: LLMModelRepository
    skill: SkillRepository
    memory_entry: MemoryEntryRepository
    llm_token_usage: LLMTokenUsageRepository
    invitation: InvitationRepository
    oauth_identity: OAuthIdentityRepository
    quota: QuotaRepository
    refresh_token: RefreshTokenRepository
    service_api_key: ServiceApiKeyRepository
    team: TeamRepository
    user: UserRepository
    artifact: ArtifactRepository
    scheduled_job: ScheduledJobRepository
    notification: NotificationRepository

    @abstractmethod
    async def commit(self):
        """提交数据库数据持久化"""
        ...

    @abstractmethod
    async def rollback(self):
        """数据库回退"""
        ...

    @abstractmethod
    async def __aenter__(self: T) -> T:
        """进入上下文管理器"""
        ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器"""
        ...
