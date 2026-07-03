#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sweep stale browser takeover waits and pause sessions for human follow-up."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select

from app.application.services.audit_service import AuditService
from app.application.services.config_provider import get_runtime_config
from app.domain.models.audit_log import AuditLog
from app.domain.models.event import MessageEvent
from app.domain.models.session import SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.hitl import TAKEOVER_PHASE, merge_pending_metadata, preserve_session_tracking_metadata
from app.infrastructure.models.session import SessionModel

logger = logging.getLogger(__name__)


async def sweep_takeover_timeouts(
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService | None = None,
) -> int:
    runtime = get_runtime_config()
    timeout_minutes = max(1, runtime.hitl.takeover_timeout_minutes)
    cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
    paused = 0

    async with uow_factory() as uow:
        stmt = (
            select(SessionModel)
            .where(
                SessionModel.status == SessionStatus.WAITING.value,
                SessionModel.pending_phase == TAKEOVER_PHASE,
            )
            .limit(100)
        )
        result = await uow.db_session.execute(stmt)  # type: ignore[attr-defined]
        rows = result.scalars().all()

    for row in rows:
        meta = row.pending_metadata or {}
        takeover = meta.get("takeover") or {}
        started_raw = takeover.get("started_at")
        if not started_raw:
            continue
        try:
            started_at = datetime.fromisoformat(str(started_raw))
        except ValueError:
            continue
        if started_at > cutoff:
            continue

        session_id = row.id
        async with uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
            if not session or session.pending_phase != TAKEOVER_PHASE:
                continue
            preserved = preserve_session_tracking_metadata(session.pending_metadata) or {}
            preserved["awaiting_human"] = {
                "reason": "takeover_timeout",
                "since": datetime.now().isoformat(),
            }
            await uow.session.set_pending_metadata(session_id, preserved)
            await uow.session.set_pending_phase(session_id, None)
            await uow.session.update_status(session_id, SessionStatus.WAITING)
            await uow.session.add_event(
                session_id,
                MessageEvent(
                    role="assistant",
                    message="接管等待已超时，会话已暂停并标记为「待人工」。请人工完成操作后发送消息继续。",
                ),
            )
            await uow.commit()

        if audit_service:
            await audit_service.record(AuditLog(
                actor_user_id=session.owner_user_id if session else None,
                action="agent_takeover_timeout",
                resource_type="session",
                resource_id=session_id,
                team_id=session.team_id if session else None,
                metadata={"timeout_minutes": timeout_minutes},
            ))
        paused += 1
        logger.info("接管超时暂停会话 session_id=%s", session_id)

    return paused


async def run_takeover_timeout_loop(
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService,
        *,
        interval_seconds: float = 30.0,
        stop_event=None,
) -> None:
    import asyncio

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            count = await sweep_takeover_timeouts(uow_factory, audit_service)
            if count:
                logger.info("接管超时 sweep 暂停 %s 个会话", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("接管超时 sweep 失败: %s", exc)
        await asyncio.sleep(interval_seconds)
