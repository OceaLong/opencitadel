import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.crypto import VersionedSecretCipher
from app.application.security.authorization_context import get_authorization_context
from app.domain.models.authorization import AuthorizationContext
from app.domain.repositories.session_resource_binding_repository import (
    SessionResourceBindingRepository,
)
from app.domain.repositories.uow import (
    IUnitOfWork,
    UnitOfWorkCleanupTimeout,
    UnitOfWorkState,
    UnitOfWorkStateError,
)
from app.infrastructure.execution.postgres_inbox import PostgresInbox
from app.infrastructure.security.db_authorization import configure_session_authorization

from .db_artifact_repository import DBArtifactRepository
from .db_audit_repository import DBAuditRepository
from .db_codebase_repository import DBCodebaseRepository
from .db_codebase_version_repository import DBCodebaseVersionRepository
from .db_file_repository import DBFileRepository
from .db_inference_binding_repository import DBInferenceBindingRepository
from .db_inference_endpoint_repository import DBInferenceEndpointRepository
from .db_inference_model_repository import DBInferenceModelRepository
from .db_integration_server_repository import (
    DBA2AServerRepository,
    DBMCPServerRepository,
)
from .db_invitation_repository import DBInvitationRepository
from .db_knowledge_base_repository import DBKnowledgeBaseRepository
from .db_knowledge_version_repository import DBKnowledgeVersionRepository
from .db_llm_token_usage_repository import DBLLMTokenUsageRepository
from .db_memory_entry_repository import DBMemoryEntryRepository
from .db_notification_repository import DBNotificationRepository
from .db_oauth_identity_repository import DBOAuthIdentityRepository
from .db_patrol_repository import DBPatrolRepository
from .db_quota_repository import DBQuotaRepository
from .db_refresh_token_repository import DBRefreshTokenRepository
from .db_scheduled_job_repository import DBScheduledJobRepository
from .db_service_api_key_repository import DBServiceApiKeyRepository
from .db_session_repository import DBSessionRepository
from .db_session_resource_binding_repository import DBSessionResourceBindingRepository
from .db_skill_repository import DBSkillRepository
from .db_team_repository import DBTeamRepository
from .db_user_repository import DBUserRepository

logger = logging.getLogger(__name__)


class DBUnitOfWork(IUnitOfWork):
    """基于Postgres数据库的UoW实例"""

    resource_bindings: SessionResourceBindingRepository

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        secret_cipher: VersionedSecretCipher,
        audit_signing_key: str,
        audit_signing_key_id: str,
        database_authorization_signing_secret: str,
        authorization_context: AuthorizationContext | None = None,
        cleanup_timeout_seconds: float = 10.0,
    ) -> None:
        """构造函数，完成UoW类初始化"""
        if cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup_timeout_seconds must be positive")
        self.session_factory = session_factory
        self._secret_cipher = secret_cipher
        self._audit_signing_key = audit_signing_key
        self._audit_signing_key_id = audit_signing_key_id
        self._database_authorization_signing_secret = database_authorization_signing_secret
        self.authorization_context = authorization_context
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._active_authorization_context: AuthorizationContext | None = None
        self.db_session: AsyncSession | None = None
        self.state = UnitOfWorkState.NEW

    async def commit(self) -> None:
        """提交数据库持久化"""
        self._require_state(UnitOfWorkState.ACTIVE)
        await self._require_session().commit()
        self.state = UnitOfWorkState.COMMITTED

    async def rollback(self) -> None:
        """数据库回退操作"""
        if self.state is UnitOfWorkState.ROLLED_BACK:
            return
        self._require_state(UnitOfWorkState.ACTIVE)
        await self._require_session().rollback()
        self.state = UnitOfWorkState.ROLLED_BACK

    async def __aenter__(self) -> Self:
        """进入UoW操作上下文管理器的逻辑"""
        self._require_state(UnitOfWorkState.NEW)
        # 1.为每个上下文开启一个新的会话
        self.db_session = self.session_factory()
        self.state = UnitOfWorkState.ACTIVE
        self._active_authorization_context = (
            self.authorization_context or get_authorization_context()
        )
        try:
            await self._configure_authorization_context()
        except BaseException:
            try:
                await self._finish_cleanup(self._close_session)
            finally:
                self.state = UnitOfWorkState.CLOSED
            raise

        # 2.初始化所有数据库仓库
        self.audit = DBAuditRepository(
            db_session=self.db_session,
            signing_key=self._audit_signing_key,
            signing_key_id=self._audit_signing_key_id,
        )
        self.codebase = DBCodebaseRepository(db_session=self.db_session)
        self.codebase_version = DBCodebaseVersionRepository(db_session=self.db_session)
        self.knowledge_base = DBKnowledgeBaseRepository(db_session=self.db_session)
        self.knowledge_version = DBKnowledgeVersionRepository(db_session=self.db_session)
        self.file = DBFileRepository(db_session=self.db_session)
        self.invitation = DBInvitationRepository(db_session=self.db_session)
        self.session = DBSessionRepository(db_session=self.db_session)
        self.inference_endpoint = DBInferenceEndpointRepository(
            db_session=self.db_session,
            cipher=self._secret_cipher,
        )
        self.inference_model = DBInferenceModelRepository(db_session=self.db_session)
        self.inference_binding = DBInferenceBindingRepository(db_session=self.db_session)
        self.skill = DBSkillRepository(db_session=self.db_session)
        self.memory_entry = DBMemoryEntryRepository(db_session=self.db_session)
        self.oauth_identity = DBOAuthIdentityRepository(db_session=self.db_session)
        self.quota = DBQuotaRepository(db_session=self.db_session)
        self.refresh_token = DBRefreshTokenRepository(db_session=self.db_session)
        self.service_api_key = DBServiceApiKeyRepository(db_session=self.db_session)
        self.team = DBTeamRepository(db_session=self.db_session)
        self.llm_token_usage = DBLLMTokenUsageRepository(db_session=self.db_session)
        self.user = DBUserRepository(db_session=self.db_session)
        self.artifact = DBArtifactRepository(db_session=self.db_session)
        self.mcp_server = DBMCPServerRepository(
            db_session=self.db_session,
            cipher=self._secret_cipher,
        )
        self.a2a_server = DBA2AServerRepository(db_session=self.db_session)
        self.scheduled_job = DBScheduledJobRepository(db_session=self.db_session)
        self.notification = DBNotificationRepository(db_session=self.db_session)
        self.resource_bindings = DBSessionResourceBindingRepository(
            db_session=self.db_session,
        )
        self.patrol = DBPatrolRepository(db_session=self.db_session)
        self.execution_commands = PostgresInbox(self.db_session)

        return self

    async def _configure_authorization_context(self) -> None:
        context = (
            self._active_authorization_context
            or self.authorization_context
            or get_authorization_context()
        )
        await configure_session_authorization(
            self._require_session(),
            context,
            signing_secret=self._database_authorization_signing_secret,
        )

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Rollback every uncommitted transaction and deterministically close."""
        del exc_type, exc_tb
        self._require_entered_state()
        cleanup_error: Exception | None = None
        cancellation: asyncio.CancelledError | None = None

        try:
            if self.state is UnitOfWorkState.ACTIVE:
                cancellation = await self._finish_cleanup(self.rollback)
        except asyncio.CancelledError as error:
            cancellation = error
        except Exception as error:  # noqa: BLE001 - transaction cleanup boundary
            cleanup_error = error
        finally:
            try:
                close_cancellation = await self._finish_cleanup(self._close_session)
                cancellation = cancellation or close_cancellation
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except Exception as error:  # noqa: BLE001 - session cleanup boundary
                cleanup_error = cleanup_error or error
            self.state = UnitOfWorkState.CLOSED

        if cancellation is not None:
            raise cancellation
        if cleanup_error is not None:
            if exc_val is None:
                raise cleanup_error
            logger.warning(
                "UoW cleanup failed while preserving body exception[%s]: %s",
                type(exc_val).__name__,
                cleanup_error,
            )

    def _require_state(self, expected: UnitOfWorkState) -> None:
        if self.state is not expected:
            raise UnitOfWorkStateError(
                f"unit of work is {self.state.value}; expected {expected.value}"
            )

    def _require_entered_state(self) -> None:
        if self.state not in {
            UnitOfWorkState.ACTIVE,
            UnitOfWorkState.COMMITTED,
            UnitOfWorkState.ROLLED_BACK,
        }:
            raise UnitOfWorkStateError(
                f"unit of work is {self.state.value}; expected an entered state"
            )

    def _require_session(self) -> AsyncSession:
        if self.db_session is None:
            raise UnitOfWorkStateError("unit of work has no active database session")
        return self.db_session

    async def _close_session(self) -> None:
        session = self._require_session()
        await session.close()
        self.db_session = None

    async def _finish_cleanup(
        self,
        operation: Callable[[], Awaitable[None]],
    ) -> asyncio.CancelledError | None:
        task = asyncio.create_task(operation())
        deadline = asyncio.get_running_loop().time() + self._cleanup_timeout_seconds
        cancellation: asyncio.CancelledError | None = None

        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise UnitOfWorkCleanupTimeout(
                    f"unit of work cleanup exceeded {self._cleanup_timeout_seconds}s"
                )
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except TimeoutError as error:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise UnitOfWorkCleanupTimeout(
                    f"unit of work cleanup exceeded {self._cleanup_timeout_seconds}s"
                ) from error

        task.result()
        return cancellation
