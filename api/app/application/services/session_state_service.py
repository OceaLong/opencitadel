#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Centralized session status transitions."""
import logging
from typing import Callable, Optional

from app.domain.external.session_list_notifier import NoopSessionListNotifier, SessionListNotifierPort
from app.domain.models.event import SessionStatusEvent
from app.domain.models.session import SessionStatus
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class SessionStateService:
    """Single authority for persisting session status changes."""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            session_list_notifier: Optional[SessionListNotifierPort] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._session_list_notifier = session_list_notifier or NoopSessionListNotifier()

    async def transition(
            self,
            session_id: str,
            status: SessionStatus,
            *,
            emit_event: bool = False,
    ) -> Optional[SessionStatusEvent]:
        async with self._uow_factory() as uow:
            await uow.session.update_status(session_id, status)
        await self._session_list_notifier.notify_sessions_changed()
        logger.debug("Session %s -> %s", session_id, status.value)
        if emit_event:
            return SessionStatusEvent(status=status.value)
        return None
