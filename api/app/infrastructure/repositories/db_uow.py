import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable
from typing import Self

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.crypto import VersionedSecretCipher
from app.application.security.authorization_context import get_authorization_context
from app.domain.evaluation.errors import DatasetUnavailable
from app.domain.models.authorization import AuthorizationContext, AuthorizationMode
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

from .db_artifact_provenance_repository import DBArtifactProvenanceRepository
from .db_artifact_repository import DBArtifactRepository
from .db_audit_repository import DBAuditRepository
from .db_evaluation_batch_repository import DBEvaluationBatchRepository
from .db_evaluation_budget_control_repository import DBEvaluationBudgetControlRepository
from .db_evaluation_budget_policy_repository import DBEvaluationBudgetPolicyRepository
from .db_evaluation_budget_repository import DBEvaluationBudgetRepository
from .db_evaluation_configuration_repository import (
    DBEvaluationConfigurationRepository,
    config_version_owner,
)
from .db_evaluation_dataset_repository import DBEvaluationDatasetRepository, dataset_version_owner
from .db_evaluation_environment_repository import DBEvaluationEnvironmentRepository
from .db_evaluation_execution_repository import DBEvaluationExecutionRepository
from .db_evaluation_judge_repository import DBEvaluationJudgeRepository
from .db_evaluation_lineage_repository import DBEvaluationLineageRepository
from .db_evaluation_recording_repository import (
    DBEvaluationRecordingRepository,
    recording_version_owner,
)
from .db_evaluation_score_repository import DBEvaluationScoreRepository
from .db_execution_comparison_repository import comparison_owner_validator
from .db_execution_content_repository import DBExecutionContentRepository
from .db_execution_usage_repository import DBExecutionUsageRepository
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
from .db_resource_pin_repository import DBResourcePinRepository
from .db_scheduled_job_repository import DBScheduledJobRepository
from .db_service_api_key_repository import DBServiceApiKeyRepository
from .db_session_repository import DBSessionRepository
from .db_session_resource_binding_repository import DBSessionResourceBindingRepository
from .db_skill_repository import DBSkillRepository
from .db_team_repository import DBTeamRepository
from .db_user_repository import DBUserRepository

logger = logging.getLogger(__name__)

# Task-local guard against nested write Units of Work (P1-2). A write UoW opened
# while another write UoW is already active *in the same asyncio task* checks out
# a second connection from the same pool while the first is still held; under load
# that exhausts the pool and deadlocks. ``contextvars`` gives us exactly the right
# scope: a child task started via ``asyncio.create_task`` copies the context at
# creation and mutates its own copy, so genuinely independent tasks never trip
# this, while a synchronous nested ``async with uow`` (callbacks, service-in-
# service reuse) does. Read-only UoWs are exempted (``read_only=True``) pending
# the broader P1-8 ReadOnlyUnitOfWork rollout that would let query-side reads nest
# safely; today no caller sets it, so the guard covers every write UoW.
_active_write_uow: contextvars.ContextVar[int] = contextvars.ContextVar(
    "opencitadel_active_write_uow",
    default=0,
)


class NestedUnitOfWorkError(UnitOfWorkStateError):
    """Raised when a write Unit of Work is entered inside another in one task."""


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
        read_only: bool = False,
    ) -> None:
        """构造函数，完成UoW类初始化"""
        if cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup_timeout_seconds must be positive")
        self.session_factory = session_factory
        self._read_only = read_only
        self._nesting_guarded = False
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
        # 0. Fail fast on a nested write UoW *before* checking out a connection,
        # so the regression surfaces as a clear error rather than a pool stall.
        self._enter_nesting_guard()
        # 1.为每个上下文开启一个新的会话
        try:
            self.db_session = self.session_factory()
        except BaseException:
            self._exit_nesting_guard()
            raise
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
                self._exit_nesting_guard()
            raise

        # 2.初始化所有数据库仓库
        self.audit = DBAuditRepository(
            db_session=self.db_session,
            signing_key=self._audit_signing_key,
            signing_key_id=self._audit_signing_key_id,
        )
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
        self.execution_content = DBExecutionContentRepository(self.db_session)
        self.evaluation_batch = DBEvaluationBatchRepository(
            self.db_session, signing_secret=self._database_authorization_signing_secret
        )
        self.evaluation_dataset = DBEvaluationDatasetRepository(self.db_session)
        from .db_evaluation_archive_repository import DBEvaluationArchiveRepository

        self.evaluation_archive = DBEvaluationArchiveRepository(
            self, signing_secret=self._database_authorization_signing_secret
        )
        self.evaluation_score = DBEvaluationScoreRepository(self)
        from .db_evaluation_summary_repository import DBEvaluationSummaryRepository

        self.evaluation_summary = DBEvaluationSummaryRepository(
            self, signing_secret=self._database_authorization_signing_secret
        )

        from .db_evaluation_review_repository import DBEvaluationReviewRepository

        self.evaluation_review = DBEvaluationReviewRepository(
            self, signing_secret=self._database_authorization_signing_secret
        )
        self.evaluation_judge = DBEvaluationJudgeRepository(self)
        self.evaluation_configuration = DBEvaluationConfigurationRepository(self.db_session)
        self.evaluation_recording = DBEvaluationRecordingRepository(self.db_session)
        self.evaluation_lineage = DBEvaluationLineageRepository(self.db_session)
        self.evaluation_physical_policy = DBEvaluationBudgetPolicyRepository(self.db_session)
        self.execution_usage = DBExecutionUsageRepository(
            self.db_session, signing_secret=self._database_authorization_signing_secret
        )
        self.evaluation_budget = DBEvaluationBudgetRepository(
            self.db_session, signing_secret=self._database_authorization_signing_secret
        )
        self.evaluation_budget_control = DBEvaluationBudgetControlRepository(self.db_session)
        self.evaluation_execution = DBEvaluationExecutionRepository(self.db_session)
        self.evaluation_environment = DBEvaluationEnvironmentRepository(self.db_session)
        self.resource_pins = DBResourcePinRepository(
            self.db_session,
            owner_validators={
                "dataset_version": dataset_version_owner,
                "recording_version": recording_version_owner,
                "config_version": config_version_owner,
                "comparison_revision": comparison_owner_validator(
                    self._active_authorization_context.principal,
                    signing_secret=self._database_authorization_signing_secret,
                ),
            },
        )
        self.artifact = DBArtifactRepository(db_session=self.db_session)
        self.artifact_provenance = DBArtifactProvenanceRepository(self.db_session)
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
            self._exit_nesting_guard()

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
        if (
            self._active_authorization_context is not None
            and self._active_authorization_context.mode is AuthorizationMode.USER
            and isinstance(exc_val, DBAPIError)
            and "evaluation_resource_archived" in str(exc_val.orig)
        ):
            raise DatasetUnavailable("evaluation_resource_archived") from exc_val

    def _enter_nesting_guard(self) -> None:
        if self._read_only:
            return
        if _active_write_uow.get() > 0:
            raise NestedUnitOfWorkError(
                "a write unit of work is already active in this task; "
                "nesting a second write UoW checks out a second pooled connection "
                "and risks pool exhaustion. Reuse the outer UoW or hoist the read "
                "out of the write transaction."
            )
        # Set the flag directly (not via Token.reset): __aenter__ and __aexit__
        # may run in different Contexts (e.g. a manually driven __aexit__ inside
        # asyncio.create_task), and a Token can only be reset in the Context that
        # created it. Because we raise above whenever the value is already > 0,
        # the depth never exceeds 1, so a plain set(0) on exit is exact.
        _active_write_uow.set(1)
        self._nesting_guarded = True

    def _exit_nesting_guard(self) -> None:
        if not self._nesting_guarded:
            return
        self._nesting_guarded = False
        _active_write_uow.set(0)

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
