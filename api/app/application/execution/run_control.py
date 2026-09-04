"""Owner-scoped commands and public-event streaming for an existing Run."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.execution.command_ingress import CommandIngress
from app.application.execution.public_projection import PublicExecutionEvent
from app.application.ports.queries import PublicProjectionPort, RunProjectionPort
from app.application.ports.streams import WakeupBroadcastPort
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
        # K4-3: the stream hangs on the kernel's ``execution:wakeup`` hint
        # stream in broadcast mode when a port is wired; the interval below is
        # the no-hint fallback (and the whole cadence when Redis is absent).
        events_wakeup: WakeupBroadcastPort | None = None,
        poll_interval_seconds: float = 1.0,
        idle_timeout_seconds: float = 120.0,
    ) -> None:
        self._commands = commands
        self._run_projection = run_projection
        self._public = public_projection
        self._events_wakeup = events_wakeup
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
        wakeup_cursor = "$"
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
            wakeup_cursor, waited = await self._await_hint(wakeup_cursor)
            idle += waited
        # Explicit idle-timeout close (D13/K4-3): tell the client this stream
        # ended for lack of events — not because the Run finished — so it can
        # distinguish "reconnect and keep waiting" from a terminal event.
        yield self._stream_timeout_event(cursor)

    async def _await_hint(self, wakeup_cursor: str) -> tuple[str, float]:
        """Wait for the next poll trigger; returns (cursor, seconds waited).

        With a wakeup port, block on the global hint stream (broadcast mode —
        unrelated events also wake us, which just means one early projection
        query) and clamp re-query cadence to the poll interval so a busy system
        never drives this session's DB rate above the fallback cadence. Without
        one, sleep the fixed interval.
        """
        if self._events_wakeup is None:
            await asyncio.sleep(self._poll_interval)
            return wakeup_cursor, self._poll_interval
        started = time.monotonic()
        batch = await self._events_wakeup.read_broadcast(
            wakeup_cursor,
            block_milliseconds=int(self._poll_interval * 1000),
        )
        if not batch.connectivity.available:
            await asyncio.sleep(self._poll_interval)
            return wakeup_cursor, time.monotonic() - started
        elapsed = time.monotonic() - started
        if batch.messages and elapsed < self._poll_interval:
            await asyncio.sleep(self._poll_interval - elapsed)
            elapsed = time.monotonic() - started
        return batch.cursor, elapsed

    @staticmethod
    def _stream_timeout_event(cursor: str | None) -> PublicExecutionEvent:
        return PublicExecutionEvent(
            cursor=cursor or "",
            event_id=uuid4(),
            event_type="stream_timeout",
            run_id=None,
            stream_id="",
            stream_version=0,
            payload={"type": "stream_timeout"},
            occurred_at=datetime.now(UTC),
        )


__all__ = ["RunControlService"]
