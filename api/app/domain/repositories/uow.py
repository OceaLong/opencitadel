from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Protocol, TypeVar

from app.domain.execution.commands import CommandEnvelope
from app.domain.models.authorization import AuthorizationContext

from .artifact_repository import ArtifactRepository
from .audit_repository import AuditRepository
from .file_repository import FileRepository
from .inference_binding_repository import InferenceBindingRepository
from .inference_endpoint_repository import InferenceEndpointRepository
from .inference_model_repository import InferenceModelRepository
from .integration_server_repository import A2AServerRepository, MCPServerRepository
from .invitation_repository import InvitationRepository
from .knowledge_base_repository import KnowledgeBaseRepository
from .knowledge_version_repository import KnowledgeVersionRepository
from .llm_token_usage_repository import LLMTokenUsageRepository
from .memory_entry_repository import MemoryEntryRepository
from .notification_repository import NotificationRepository
from .oauth_identity_repository import OAuthIdentityRepository
from .patrol_repository import PatrolRepository
from .quota_repository import QuotaRepository
from .refresh_token_repository import RefreshTokenRepository
from .scheduled_job_repository import ScheduledJobRepository
from .service_api_key_repository import ServiceApiKeyRepository
from .session_repository import SessionRepository
from .session_resource_binding_repository import SessionResourceBindingRepository
from .skill_repository import SkillRepository
from .team_repository import TeamRepository
from .user_repository import UserRepository

T = TypeVar("T", bound="IUnitOfWork")


class UnitOfWorkState(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    CLOSED = "closed"


class UnitOfWorkStateError(RuntimeError):
    """Raised when an operation violates the UoW transaction lifecycle."""


class UnitOfWorkCleanupTimeout(RuntimeError):
    """Raised when rollback or close cannot finish inside the cleanup bound."""


class ExecutionCommandSink(Protocol):
    async def receive(self, command: CommandEnvelope) -> bool: ...


class UnitOfWorkFactory(Protocol):
    def __call__(
        self,
        authorization_context: AuthorizationContext | None = None,
    ) -> "IUnitOfWork": ...


class IUnitOfWork(ABC):
    """Uow模式协议接口"""

    audit: AuditRepository
    knowledge_base: KnowledgeBaseRepository
    knowledge_version: KnowledgeVersionRepository
    file: FileRepository
    session: SessionRepository
    inference_endpoint: InferenceEndpointRepository
    inference_model: InferenceModelRepository
    inference_binding: InferenceBindingRepository
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
    mcp_server: MCPServerRepository
    a2a_server: A2AServerRepository
    scheduled_job: ScheduledJobRepository
    notification: NotificationRepository
    resource_bindings: SessionResourceBindingRepository
    patrol: PatrolRepository
    execution_commands: ExecutionCommandSink
    state: UnitOfWorkState

    @abstractmethod
    async def commit(self) -> None:
        """提交数据库数据持久化"""
        ...

    @abstractmethod
    async def rollback(self) -> None:
        """数据库回退"""
        ...

    @abstractmethod
    async def __aenter__(self: T) -> T:
        """进入上下文管理器"""
        ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出上下文管理器"""
        ...
