"""Owner-scoped commands and public-event streaming for an existing Run."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.execution.command_ingress import CommandIngress
from app.application.execution.public_projection import PublicExecutionEvent
from app.application.ports.queries import PublicProjectionPort, RunProjectionPort
from app.domain.execution.commands import CommandContext, RegisteredCommand
from app.domain.models.scope import OwnerScope

_TERMINAL_EVENTS = frozenset({"RunCompleted", "RunFailed", "RunCancelled"})


class RunControlService:
    def __init__(
        self,
        *,
        commands: CommandIngress,
        run_projection: RunProjectionPort,
        public_projection: PublicProjectionPort,
        poll_interval_seconds: float = 0.2,
        idle_timeout_seconds: float = 120.0,
    ) -> None:
        self._commands = commands
        self._run_projection = run_projection
        self._public = public_projection
        self._poll_interval = poll_interval_seconds
        self._idle_timeout = idle_timeout_seconds

    async def cancel_source(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
        reason: str,
    ) -> UUID | None:
        run_id = await self._run_projection.latest_active_run_id(
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            owner_scope=owner_scope,
        )
        if run_id is None:
            return None
        owner_user_id = None if owner_scope.team_id else owner_scope.user_id
        await self._commands.submit(
            RegisteredCommand(
                command_id=uuid4(),
                command_type="CancelRun",
                run_id=run_id,
                expected_stream_version=None,
                payload={"reason": reason},
            ),
            CommandContext(
                owner_user_id=owner_user_id,
                team_id=owner_scope.team_id,
                correlation_id=run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
            ),
        )
        return run_id

    async def stream_source(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
        after: str | None = None,
    ) -> AsyncIterator[PublicExecutionEvent]:
        cursor = after
        idle = 0.0
        while idle < self._idle_timeout:
            page = await self._public.list_events(
                source_entity_type=source_entity_type,
                source_entity_id=source_entity_id,
                owner_scope=owner_scope,
                after=cursor,
                limit=100,
            )
            if page.events:
                idle = 0.0
                for event in page.events:
                    cursor = event.cursor
                    yield event
                    if event.event_type in _TERMINAL_EVENTS:
                        return
                continue
            await asyncio.sleep(self._poll_interval)
            idle += self._poll_interval


__all__ = ["RunControlService"]
