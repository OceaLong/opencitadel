#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.security.authorization_context import get_authorization_context
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.observability.logging_context import get_request_id

_AUTHORIZATION_SQL = text(
    """
    SELECT
        set_config('app.auth_mode', :auth_mode, true),
        set_config('app.user_id', :user_id, true),
        set_config('app.team_id', :team_id, true),
        set_config('app.is_admin', :is_admin, true),
        set_config('app.request_id', :request_id, true),
        set_config('app.system_actor', :system_actor, true)
    """
)


def configure_sync_system_authorization(
    connection: Connection,
    *,
    actor: str,
) -> None:
    """Authorize schema/data migration SQL inside its current transaction."""
    system_actor = actor.strip()
    if not system_actor:
        raise ValueError("system migration actor must not be empty")
    connection.execute(
        _AUTHORIZATION_SQL,
        {
            "auth_mode": "system",
            "user_id": "",
            "team_id": "",
            "is_admin": "false",
            "request_id": "",
            "system_actor": system_actor,
        },
    )


async def configure_session_authorization(
    session: AsyncSession,
    context: Optional[AuthorizationContext] = None,
) -> AuthorizationContext:
    """Bind an immutable authorization context to the current DB transaction."""
    resolved = context or get_authorization_context()
    await session.execute(
        _AUTHORIZATION_SQL,
        {
            "auth_mode": resolved.mode.value,
            "user_id": resolved.user_id or "",
            "team_id": resolved.team_id or "",
            "is_admin": "true" if resolved.is_admin else "false",
            "request_id": resolved.request_id or get_request_id() or "",
            "system_actor": resolved.system_actor,
        },
    )
    return resolved
