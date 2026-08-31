import hashlib
import hmac
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.application.execution.admission import RunAdmissionService
from app.application.ports.crypto import SecretCipherError, VersionedSecretCipher
from app.application.ports.queries import RunProjectionPort
from app.application.services.notification_service import NotificationService
from app.application.services.patrol_run_service import PatrolRunService
from app.application.services.resource_binding_service import (
    ResourceBindingService,
)
from app.application.services.resource_guard_service import (
    ResourceGuardService,
)
from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.domain.errors import BadRequestError
from app.domain.execution.run import RunFamily, RunStatus
from app.domain.models.patrol import PatrolTriggerType
from app.domain.models.scheduled_job import (
    NotifyChannel,
    ScheduledJob,
    ScheduledRunStatus,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session, SessionMode, SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.schedule_utils import (
    compute_next_run,
    render_prompt_template,
    validate_trigger_spec,
)
from app.domain.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

_TERMINAL_STATUS_MAP = {
    "completed": ScheduledRunStatus.COMPLETED,
    "failed": ScheduledRunStatus.FAILED,
    "cancelled": ScheduledRunStatus.CANCELLED,
}


class _SchedulerPolicyDenied(RuntimeError):
    """Private rollback signal for a live scheduler tightening."""


class ScheduledJobService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        patrol_run_service: PatrolRunService,
        resource_guard: ResourceGuardService,
        resource_binding_service: ResourceBindingService,
        run_admission_service: RunAdmissionService,
        run_projection: RunProjectionPort,
        policy_reader: OperationsPolicyReader,
        notification_service: NotificationService,
        secret_cipher: VersionedSecretCipher,
    ) -> None:
        self._uow_factory = uow_factory
        self._patrol_run_service = patrol_run_service
        self._resource_guard = resource_guard
        self._resource_binding_service = resource_binding_service
        self._run_admission = run_admission_service
        self._run_projection = run_projection
        self._policy_reader = policy_reader
        self._notification_service = notification_service
        self._secret_cipher = secret_cipher

    async def _scheduler_enabled(self) -> bool:
        active = await self._policy_reader.active_operations(
            require_fresh=True,
            now=utc_now(),
        )
        return active.revision.policy.scheduler.enabled

    def _encrypt_webhook_secret(self, secret: str) -> str:
        return self._secret_cipher.encrypt_versioned(secret)

    def _decrypt_webhook_secret(self, stored: str) -> str | None:
        if not stored:
            return None
        try:
            return self._secret_cipher.decrypt_versioned(stored)
        except SecretCipherError:
            logger.warning("Webhook secret 解密失败，请轮换密钥")
            return None

    @staticmethod
    def _scope_for_job(job: ScheduledJob) -> OwnerScope:
        if job.team_id:
            return OwnerScope.team(job.owner_user_id, job.team_id)
        return OwnerScope.personal(job.owner_user_id)

    @staticmethod
    async def _validate_resource_access(
        uow: IUnitOfWork,
        job: ScheduledJob,
        scope: OwnerScope,
    ) -> None:
        checks = (
            (job.model_id, uow.inference_model.get_by_id, "模型"),
            (job.skill_id, uow.skill.get_by_id, "Skill"),
            (job.codebase_id, uow.codebase.get_by_id, "代码库"),
            (job.knowledge_base_id, uow.knowledge_base.get_kb, "知识库"),
        )
        for resource_id, getter, label in checks:
            if resource_id and await getter(resource_id, scope=scope) is None:
                raise BadRequestError(f"{label}[{resource_id}]不存在或不可访问")

    @staticmethod
    def verify_webhook_signature(secret: str, body: bytes, signature: str) -> bool:
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    async def create_job(
        self,
        owner_user_id: str,
        name: str,
        trigger_type: str,
        trigger_spec: str,
        prompt_template: str,
        *,
        skill_id: str | None = None,
        model_id: str | None = None,
        codebase_id: str | None = None,
        knowledge_base_id: str | None = None,
        notify_channels: list[NotifyChannel] | None = None,
        operator_scope: str | None = None,
        operator_domains: list[str] | None = None,
        enabled: bool = True,
        timezone: str = "UTC",
        source_type: str = "generic",
        source_id: str | None = None,
        scope: OwnerScope | None = None,
    ) -> tuple[ScheduledJob, str | None]:
        if trigger_type != "webhook":
            validate_trigger_spec(trigger_type, trigger_spec)
        webhook_secret: str | None = None
        job = ScheduledJob(
            name=name,
            owner_user_id=owner_user_id,
            team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
            trigger_type=trigger_type,  # type: ignore[arg-type]
            trigger_spec=trigger_spec,
            prompt_template=prompt_template,
            skill_id=skill_id,
            model_id=model_id,
            codebase_id=codebase_id,
            knowledge_base_id=knowledge_base_id,
            notify_channels=notify_channels or [],
            operator_scope=operator_scope,
            operator_domains=list(operator_domains or []),
            enabled=enabled,
            timezone=timezone,
            source_type=source_type,  # type: ignore[arg-type]
            source_id=source_id,
        )
        if trigger_type == "webhook":
            webhook_secret = secrets.token_urlsafe(32)
            job.webhook_token = secrets.token_urlsafe(16)
            job.webhook_secret_hash = self._encrypt_webhook_secret(webhook_secret)
            job.next_run_at = None
        else:
            job.next_run_at = compute_next_run(
                trigger_type, trigger_spec, timezone_name=job.timezone
            )

        async with self._uow_factory() as uow:
            await self._validate_resource_access(
                uow,
                job,
                scope or self._scope_for_job(job),
            )
            await uow.scheduled_job.save(job)
            await uow.commit()
        return job, webhook_secret

    async def list_jobs(self, scope: OwnerScope) -> list[ScheduledJob]:
        async with self._uow_factory() as uow:
            return await uow.scheduled_job.list_for_scope(scope)

    async def get_job(
        self,
        job_id: str,
        scope: OwnerScope | None = None,
    ) -> ScheduledJob | None:
        async with self._uow_factory() as uow:
            return await uow.scheduled_job.get_by_id(job_id, scope=scope)

    async def manual_trigger(
        self,
        job_id: str,
        owner_user_id: str,
        *,
        scope: OwnerScope | None = None,
    ) -> str | None:
        job = await self.get_job(job_id, scope=scope)
        if not job:
            return None
        if not job.enabled:
            raise ValueError("任务已禁用")
        return await self.trigger_job(job)

    async def patch_job(
        self,
        job_id: str,
        scope: OwnerScope,
        **fields,
    ) -> ScheduledJob | None:
        async with self._uow_factory() as uow:
            job = await uow.scheduled_job.get_by_id(job_id, scope=scope)
            if not job:
                return None
            for key, value in fields.items():
                if value is None:
                    continue
                if key == "notify_channels":
                    job.notify_channels = value
                else:
                    setattr(job, key, value)
            job = ScheduledJob.model_validate(job.model_dump(mode="python"))
            if job.trigger_type != "webhook":
                validate_trigger_spec(job.trigger_type, job.trigger_spec)
                job.next_run_at = compute_next_run(
                    job.trigger_type, job.trigger_spec, timezone_name=job.timezone
                )
            job.updated_at = datetime.now(UTC)
            await self._validate_resource_access(uow, job, scope)
            await uow.scheduled_job.save(job)
            await uow.commit()
            return job

    async def delete_job(self, job_id: str, scope: OwnerScope | None = None) -> None:
        async with self._uow_factory() as uow:
            if scope is not None and not await uow.scheduled_job.get_by_id(job_id, scope=scope):
                return
            await uow.scheduled_job.delete_by_id(job_id)
            await uow.commit()

    async def rotate_webhook_secret(
        self,
        job_id: str,
        scope: OwnerScope | None = None,
    ) -> tuple[str | None, str | None]:
        secret = secrets.token_urlsafe(32)
        async with self._uow_factory() as uow:
            job = await uow.scheduled_job.get_by_id(job_id, scope=scope)
            if not job:
                return None, None
            job.webhook_secret_hash = self._encrypt_webhook_secret(secret)
            if not job.webhook_token:
                job.webhook_token = secrets.token_urlsafe(16)
            await uow.scheduled_job.save(job)
            await uow.commit()
            return secret, job.webhook_token

    async def record_trigger_failure(self, job: ScheduledJob, error: str) -> None:
        job.last_run_status = ScheduledRunStatus.FAILED
        job.last_run_error = error[:2000] if error else None
        job.updated_at = datetime.now(UTC)
        if job.trigger_type != "webhook":
            retry_at = compute_next_run(
                job.trigger_type, job.trigger_spec, timezone_name=job.timezone
            )
            if retry_at is None or retry_at <= datetime.now(UTC):
                retry_at = datetime.now(UTC) + timedelta(seconds=60)
            job.next_run_at = retry_at
        async with self._uow_factory() as uow:
            await uow.scheduled_job.save(job)
            await uow.commit()
        logger.warning("定时任务触发失败 job=%s error=%s", job.id, error)

    async def reconcile_running_runs(
        self,
        *,
        limit: int = 100,
    ) -> int:
        """Project authoritative Automation Run terminals onto job summaries."""
        async with self._uow_factory() as uow:
            jobs = await uow.scheduled_job.list_running(limit=limit)
        reconciled = 0
        for job in jobs:
            if job.last_execution_run_id is None or not job.last_run_session_id:
                continue
            status = await self._run_projection.status_for_run(
                run_id=job.last_execution_run_id,
                owner_scope=self._scope_for_job(job),
            )
            if status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                continue
            await self.on_session_terminal(
                job.last_run_session_id,
                status.value,
            )
            reconciled += 1
        return reconciled

    async def on_session_terminal(
        self,
        session_id: str,
        status: str,
    ) -> None:
        normalized = _TERMINAL_STATUS_MAP.get(status.lower())
        if not normalized:
            return
        async with self._uow_factory() as uow:
            job = await uow.scheduled_job.get_by_last_run_session_id(session_id)
            if not job:
                return
            job.last_run_status = normalized
            job.updated_at = datetime.now(UTC)
            await uow.scheduled_job.save(job)
            await uow.commit()

        if normalized == "completed":
            fallback_message = f'Scheduled job "{job.name}" completed'
            await self._notification_service.send(
                job.owner_user_id,
                "job_complete",
                fallback_message,
                i18n_key="notifications.scheduledJobCompleted",
                i18n_params={"jobName": job.name},
                session_id=session_id,
                job_id=job.id,
            )
            if job.notify_channels:
                await self._notification_service.dispatch_notify_channels(
                    job.owner_user_id,
                    self._scope_for_job(job),
                    job.notify_channels_dict(),
                    fallback_message,
                )

    async def trigger_job(
        self,
        job: ScheduledJob,
        payload: dict | None = None,
        *,
        firing_id: str | None = None,
        fired_at: datetime | None = None,
    ) -> str | None:
        if not await self._scheduler_enabled():
            return None

        fired_at = fired_at or job.next_run_at or datetime.now(UTC)
        firing_id = firing_id or str(uuid.uuid4())

        if job.source_type == "patrol_pack":
            if not job.source_id or self._patrol_run_service is None:
                raise BadRequestError("Patrol scheduled job binding is unavailable")
            job.last_run_at = fired_at
            job.last_run_status = ScheduledRunStatus.RUNNING
            job.last_run_error = None
            if job.trigger_type != "webhook":
                job.next_run_at = compute_next_run(
                    job.trigger_type,
                    job.trigger_spec,
                    timezone_name=job.timezone,
                )
            if not await self._scheduler_enabled():
                return None
            run = await self._patrol_run_service.trigger_pack(
                job.source_id,
                self._scope_for_job(job),
                job.owner_user_id,
                idempotency_key=f"schedule:{job.id}:{firing_id}",
                trigger_type=PatrolTriggerType.SCHEDULE,
                automation_job=job,
                automation_firing_id=firing_id,
                automation_fired_at=fired_at,
            )
            return run.session_id

        try:
            async with self._uow_factory() as uow:
                locked_job = await uow.scheduled_job.get_by_id(
                    job.id,
                    for_update=True,
                )
                if locked_job is None or not locked_job.enabled:
                    return None
                if locked_job.last_run_at == fired_at and locked_job.last_run_session_id:
                    return locked_job.last_run_session_id
                if locked_job.last_run_status == ScheduledRunStatus.RUNNING:
                    logger.info("跳过仍在运行中的 job=%s", job.id)
                    return locked_job.last_run_session_id
                job = locked_job
                scope = self._scope_for_job(job)
                validated_resources = None
                if job.codebase_id or job.knowledge_base_id:
                    if not self._resource_guard or not self._resource_binding_service:
                        raise BadRequestError("Scheduled job resource binding is unavailable")
                    validated_resources = await self._resource_guard.validate_session_request(
                        mode=SessionMode.AGENT,
                        codebase_id=job.codebase_id,
                        codebase_version_id=None,
                        knowledge_base_id=job.knowledge_base_id,
                        knowledge_base_version_id=None,
                        scope=scope,
                    )
                prompt = render_prompt_template(job.prompt_template, payload)
                session = Session(
                    title=f"[定时] {job.name}",
                    model_id=job.model_id,
                    skill_id=job.skill_id,
                    owner_user_id=job.owner_user_id,
                    team_id=job.team_id,
                    operator_scope=job.operator_scope,
                    operator_domains=list(job.operator_domains or []),
                    mode=SessionMode.AGENT,
                    status=SessionStatus.PENDING,
                )
                await self._validate_resource_access(uow, job, scope)
                if validated_resources:
                    for version in validated_resources.versions:
                        binding = await self._resource_binding_service.bind_initial_resolved(
                            uow,
                            session_id=session.id,
                            resolved=version,
                            scope=scope,
                            actor_id=scope.user_id,
                        )
                        session.resource_bindings.append(binding.to_projection())
                await uow.session.save(session)
                job.last_run_at = fired_at
                job.last_run_status = ScheduledRunStatus.RUNNING
                job.last_run_session_id = session.id
                job.last_run_error = None
                if job.trigger_type != "webhook":
                    job.next_run_at = compute_next_run(
                        job.trigger_type,
                        job.trigger_spec,
                        timezone_name=job.timezone,
                    )
                await uow.scheduled_job.save(job)
                if not await self._scheduler_enabled():
                    raise _SchedulerPolicyDenied
                execution_run_id = await self._run_admission.admit(
                    family=RunFamily.AUTOMATION,
                    source_entity_type="scheduled_job",
                    source_entity_id=job.id,
                    owner_scope=scope,
                    private_input={
                        "message": prompt,
                        "model_id": job.model_id,
                        "skill_id": job.skill_id,
                        "session_id": session.id,
                        "child_family": RunFamily.AGENT.value,
                        "child_source_entity_type": "session",
                        "child_source_entity_id": session.id,
                    },
                    public_input={
                        "firing_id": firing_id,
                        "session_id": session.id,
                    },
                    idempotency_key=f"scheduled:{job.id}:{firing_id}",
                    command_sink=uow.execution_commands,
                )
                job.last_execution_run_id = execution_run_id
                await uow.scheduled_job.save(job)
                await uow.commit()
        except _SchedulerPolicyDenied:
            return None

        fallback_message = f'Scheduled job "{job.name}" started'
        await self._notification_service.send(
            job.owner_user_id,
            "job_started",
            fallback_message,
            i18n_key="notifications.scheduledJobStarted",
            i18n_params={"jobName": job.name},
            session_id=session.id,
            job_id=job.id,
        )
        if job.notify_channels:
            await self._notification_service.dispatch_notify_channels(
                job.owner_user_id,
                self._scope_for_job(job),
                job.notify_channels_dict(),
                fallback_message,
            )
        return session.id

    async def trigger_webhook(
        self,
        token: str,
        body: bytes,
        signature: str,
        payload: dict,
    ) -> tuple[str | None, str | None]:
        """Returns (session_id, error_code). error_code: not_found|unauthorized|duplicate."""
        async with self._uow_factory() as uow:
            job = await uow.scheduled_job.get_by_webhook_token(token)
        if not job or not job.enabled:
            return None, "not_found"

        secret = self._decrypt_webhook_secret(job.webhook_secret_hash or "")
        if not secret:
            return None, "unauthorized"
        if not signature or not self.verify_webhook_signature(secret, body, signature):
            logger.warning("Webhook signature missing or invalid job=%s", job.id)
            return None, "unauthorized"

        active = await self._policy_reader.active_operations(
            require_fresh=True,
            now=utc_now(),
        )
        body_hash = hashlib.sha256(body).hexdigest()
        ttl = active.revision.policy.scheduler.webhook_idempotency_ttl_seconds
        bucket = int(datetime.now(UTC).timestamp() // ttl)
        firing_id = f"webhook:{body_hash}:{bucket}"
        fired_at = datetime.fromtimestamp(bucket * ttl, UTC)
        if job.last_run_at == fired_at and job.last_run_session_id:
            return job.last_run_session_id, "duplicate"

        session_id = await self.trigger_job(
            job,
            payload,
            firing_id=firing_id,
            fired_at=fired_at,
        )
        return session_id, None
