#!/usr/bin/env python
# -*- coding: utf-8 -*-
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from core.config import get_settings

from app.application.errors.exceptions import BadRequestError
from app.application.services.config_provider import get_runtime_config
from app.domain.models.scheduled_job import ScheduledJob, NotifyChannel
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session, SessionMode, SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.schedule_utils import compute_next_run, render_prompt_template
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import get_task_state
from app.domain.models.event import MessageEvent
from app.infrastructure.security.api_key_cipher import ApiKeyCipher, ApiKeyCipherError
from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)

_TERMINAL_STATUS_MAP = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


class ScheduledJobService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    @staticmethod
    def _cipher() -> ApiKeyCipher:
        settings = get_settings()
        return ApiKeyCipher(
            settings.api_key_secret,
            key_id=settings.api_key_secret_id,
            previous_secrets=settings.api_key_previous_secrets,
        )

    def _encrypt_webhook_secret(self, secret: str) -> str:
        return self._cipher().encrypt(secret)

    def _decrypt_webhook_secret(self, stored: str) -> Optional[str]:
        if not stored:
            return None
        if ApiKeyCipher.looks_like_fernet_token(stored):
            try:
                return self._cipher().decrypt_or_raise(stored)
            except ApiKeyCipherError:
                logger.warning("Webhook secret 解密失败，请轮换密钥")
                return None
        # Legacy SHA256-only storage cannot verify HMAC; force rotate.
        logger.warning("Webhook job 使用旧版密钥存储，请轮换 webhook secret")
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
            (job.model_id, uow.llm_model.get_by_id, "模型"),
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
            skill_id: Optional[str] = None,
            model_id: Optional[str] = None,
            codebase_id: Optional[str] = None,
            knowledge_base_id: Optional[str] = None,
            notify_channels: Optional[List[NotifyChannel]] = None,
            operator_scope: Optional[str] = None,
            operator_domains: Optional[List[str]] = None,
            gate_profile: Optional[str] = None,
            enabled: bool = True,
            scope: Optional[OwnerScope] = None,
    ) -> tuple[ScheduledJob, Optional[str]]:
        webhook_secret: Optional[str] = None
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
            gate_profile=gate_profile,
            enabled=enabled,
        )
        if trigger_type == "webhook":
            webhook_secret = secrets.token_urlsafe(32)
            job.webhook_token = secrets.token_urlsafe(16)
            job.webhook_secret_hash = self._encrypt_webhook_secret(webhook_secret)
            job.next_run_at = None
        else:
            job.next_run_at = compute_next_run(trigger_type, trigger_spec)

        async with self._uow_factory() as uow:
            await self._validate_resource_access(
                uow,
                job,
                scope or self._scope_for_job(job),
            )
            await uow.scheduled_job.save(job)
            await uow.commit()
        return job, webhook_secret

    async def list_jobs(self, scope: OwnerScope) -> List[ScheduledJob]:
        async with self._uow_factory() as uow:
            return await uow.scheduled_job.list_for_scope(scope)

    async def get_job(
            self,
            job_id: str,
            scope: Optional[OwnerScope] = None,
    ) -> Optional[ScheduledJob]:
        async with self._uow_factory() as uow:
            return await uow.scheduled_job.get_by_id(job_id, scope=scope)

    async def manual_trigger(
            self,
            job_id: str,
            owner_user_id: str,
            *,
            scope: Optional[OwnerScope] = None,
            notification_service=None,
            mcp_pool=None,
            app_config=None,
    ) -> Optional[str]:
        job = await self.get_job(job_id, scope=scope)
        if not job:
            return None
        if not job.enabled:
            raise ValueError("任务已禁用")
        return await self.trigger_job(
            job,
            notification_service=notification_service,
            mcp_pool=mcp_pool,
            app_config=app_config,
        )

    async def update_job(self, job: ScheduledJob) -> ScheduledJob:
        if job.trigger_type != "webhook":
            job.next_run_at = compute_next_run(job.trigger_type, job.trigger_spec)
        job.updated_at = datetime.now()
        async with self._uow_factory() as uow:
            await self._validate_resource_access(uow, job, self._scope_for_job(job))
            await uow.scheduled_job.save(job)
            await uow.commit()
        return job

    async def patch_job(
            self,
            job_id: str,
            scope: OwnerScope,
            **fields,
    ) -> Optional[ScheduledJob]:
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
            if job.trigger_type != "webhook":
                job.next_run_at = compute_next_run(job.trigger_type, job.trigger_spec)
            job.updated_at = datetime.now()
            await self._validate_resource_access(uow, job, scope)
            await uow.scheduled_job.save(job)
            await uow.commit()
            return job

    async def delete_job(self, job_id: str, scope: Optional[OwnerScope] = None) -> None:
        async with self._uow_factory() as uow:
            if scope is not None and not await uow.scheduled_job.get_by_id(job_id, scope=scope):
                return
            await uow.scheduled_job.delete_by_id(job_id)
            await uow.commit()

    async def rotate_webhook_secret(
            self,
            job_id: str,
            scope: Optional[OwnerScope] = None,
    ) -> tuple[Optional[str], Optional[str]]:
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
        job.last_run_status = "failed"
        job.last_run_error = error[:2000] if error else None
        job.updated_at = datetime.now()
        if job.trigger_type != "webhook":
            retry_at = compute_next_run(job.trigger_type, job.trigger_spec)
            if retry_at is None or retry_at <= datetime.now():
                retry_at = datetime.now() + timedelta(seconds=60)
            job.next_run_at = retry_at
        async with self._uow_factory() as uow:
            await uow.scheduled_job.save(job)
            await uow.commit()
        logger.warning("定时任务触发失败 job=%s error=%s", job.id, error)

    async def on_session_terminal(
            self,
            session_id: str,
            status: str,
            *,
            notification_service=None,
            mcp_pool=None,
            app_config=None,
    ) -> None:
        normalized = _TERMINAL_STATUS_MAP.get(status.lower())
        if not normalized:
            return
        async with self._uow_factory() as uow:
            job = await uow.scheduled_job.get_by_last_run_session_id(session_id)
            if not job:
                return
            job.last_run_status = normalized
            job.updated_at = datetime.now()
            await uow.scheduled_job.save(job)
            await uow.commit()

        if notification_service and normalized == "completed":
            fallback_message = f'Scheduled job "{job.name}" completed'
            await notification_service.send(
                job.owner_user_id,
                "job_complete",
                fallback_message,
                i18n_key="notifications.scheduledJobCompleted",
                i18n_params={"jobName": job.name},
                session_id=session_id,
                job_id=job.id,
            )
            if job.notify_channels and mcp_pool and app_config:
                await notification_service.send_im_via_mcp(
                    job.owner_user_id,
                    job.notify_channels_dict(),
                    fallback_message,
                    mcp_pool,
                    app_config,
                )

    async def trigger_job(
            self,
            job: ScheduledJob,
            payload: Optional[dict] = None,
            *,
            notification_service=None,
            mcp_pool=None,
            app_config=None,
    ) -> Optional[str]:
        config = get_runtime_config()
        if not config.scheduler.enabled:
            return None

        sched_cfg = config.scheduler
        if job.last_run_status == "running" and job.last_run_at:
            stale_after = timedelta(seconds=max(sched_cfg.leader_lease_seconds * 2, 120))
            if datetime.now() - job.last_run_at < stale_after:
                logger.info("跳过仍在运行中的 job=%s", job.id)
                return job.last_run_session_id

        async with self._uow_factory() as uow:
            await self._validate_resource_access(uow, job, self._scope_for_job(job))

        prompt = render_prompt_template(job.prompt_template, payload)
        session = Session(
            title=f"[定时] {job.name}",
            model_id=job.model_id,
            skill_id=job.skill_id,
            codebase_id=job.codebase_id,
            knowledge_base_id=job.knowledge_base_id,
            owner_user_id=job.owner_user_id,
            team_id=job.team_id,
            operator_scope=job.operator_scope,
            operator_domains=list(job.operator_domains or []),
            gate_profile=job.gate_profile or ("standard" if job.operator_scope else None),
            mode=SessionMode.AGENT,
        )
        task_state = get_task_state()
        task = await RedisStreamTask.create_for_session(session.id)
        session.task_id = task.id

        async with self._uow_factory() as uow:
            await uow.session.save(session)
            await uow.session.update_status(session.id, SessionStatus.RUNNING)
            await uow.commit()

        message_event = MessageEvent(role="user", message=prompt)
        await task.input_stream.put(message_event.model_dump_json())
        await task.dispatch_to_worker()

        job.last_run_at = datetime.now()
        job.last_run_status = "running"
        job.last_run_session_id = session.id
        job.last_run_error = None
        if job.trigger_type != "webhook":
            job.next_run_at = compute_next_run(job.trigger_type, job.trigger_spec)
        async with self._uow_factory() as uow:
            await uow.scheduled_job.save(job)
            await uow.commit()

        if notification_service:
            fallback_message = f'Scheduled job "{job.name}" started'
            await notification_service.send(
                job.owner_user_id,
                "job_started",
                fallback_message,
                i18n_key="notifications.scheduledJobStarted",
                i18n_params={"jobName": job.name},
                session_id=session.id,
                job_id=job.id,
            )
            if job.notify_channels and mcp_pool and app_config:
                await notification_service.send_im_via_mcp(
                    job.owner_user_id,
                    job.notify_channels_dict(),
                    fallback_message,
                    mcp_pool,
                    app_config,
                )
        return session.id

    async def trigger_webhook(
            self,
            token: str,
            body: bytes,
            signature: str,
            payload: dict,
            *,
            notification_service=None,
            mcp_pool=None,
            app_config=None,
    ) -> tuple[Optional[str], Optional[str]]:
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

        body_hash = hashlib.sha256(body).hexdigest()
        idem_key = f"webhook:idem:{token}:{body_hash}"
        ttl = get_runtime_config().scheduler.webhook_idempotency_ttl_seconds
        redis = get_redis()
        if not await redis.client.set(idem_key, "1", nx=True, ex=ttl):
            return job.last_run_session_id, "duplicate"

        session_id = await self.trigger_job(
            job,
            payload,
            notification_service=notification_service,
            mcp_pool=mcp_pool,
            app_config=app_config,
        )
        return session_id, None
