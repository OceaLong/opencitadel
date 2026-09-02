"""Timer worker tests for stable command identity and fenced completion."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.kernel.application.timer_worker import TimerClaim, TimerWorker
from app.kernel.domain.types import OwnerScopeRef, Workflow

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


class RecordingCommandSink:
    def __init__(self) -> None:
        self.commands = []

    async def submit(self, command, authorization):
        self.commands.append(command)
        return object()


class TimerStore:
    def __init__(self, claim: TimerClaim) -> None:
        self.claim = claim
        self.fired = []

    async def claim_due(self, *, worker_id, now, limit, lease_seconds):
        return (self.claim,)

    async def mark_fired(self, timer_id, claim_generation, *, now):
        self.fired.append((timer_id, claim_generation))
        return True


@pytest.mark.asyncio
async def test_timer_uses_its_identity_as_the_idempotent_command_id() -> None:
    """A crash after submit but before mark_fired must not duplicate the timeout."""

    timer_id = UUID(int=6200)
    claim = TimerClaim(
        timer_id=timer_id,
        run_id=UUID(int=6201),
        workflow=Workflow.AGENT,
        command_type="ExpireApproval",
        command_payload={"approval_id": str(UUID(int=6202))},
        owner_scope=OwnerScopeRef.team("team-1"),
        claim_generation=3,
    )
    store = TimerStore(claim)
    sink = RecordingCommandSink()
    worker = TimerWorker(store=store, command_sink=sink, worker_id="timer-1")

    assert await worker.run_once(now=NOW) == 1
    assert sink.commands[0].command_id == timer_id
    assert sink.commands[0].type == "ExpireApproval"
    assert sink.commands[0].payload["timer_claim_generation"] == 3
    assert store.fired == [(timer_id, 3)]
