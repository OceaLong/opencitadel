#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional, Protocol, runtime_checkable

from app.domain.models.event import Event
from app.domain.models.session import SessionStatus


@runtime_checkable
class SessionStatePort(Protocol):
    async def transition(
            self,
            session_id: str,
            status: SessionStatus,
            *,
            emit_event: bool = True,
    ) -> Optional[Event]:
        ...
