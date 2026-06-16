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

_PROTECTED_TERMINAL_STATUSES = {
    SessionStatus.CANCELLED,
    SessionStatus.FAILED,
}


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
        current_status = None
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id)
            if session:
                current_status = session.status
            if (
                current_status in _PROTECTED_TERMINAL_STATUSES
                and status not in _PROTECTED_TERMINAL_STATUSES
            ):
                logger.debug(
                    "Skip session %s transition %s -> %s",
                    session_id,
                    current_status.value,
                    status.value,
                )
                if emit_event:
                    return SessionStatusEvent(status=current_status.value)
                return None
            await uow.session.update_status(session_id, status)
        await self._session_list_notifier.notify_sessions_changed()
        logger.debug("Session %s -> %s", session_id, status.value)
        if emit_event:
            return SessionStatusEvent(status=status.value)
        return None
