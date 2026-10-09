"""Offline SQL-orchestrator clock boundary checks, not database lease proof."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from scripts.test_benchmark_execution_visualization import _plan

from app.domain.execution.events import StoredEvent
from app.domain.execution.run import RunAggregate
from app.domain.execution.store import AppendResult, calculate_event_hash, verify_stream
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_inbox import InboxClaim
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator


class Session:
    def __init__(self):
        self.info = {}
        self.rows = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(rowcount=0)

    async def commit(self):
        self.committed = True

    def add(self, row):
        self.rows.append(row)


class Inbox:
    async def claim(self, command, *, now, claim_ttl):
        self.command = command
        self.deadline = now + claim_ttl
        self.claimed_at = now
        return InboxClaim(status="claimed", generation=1)

    async def complete(self, result, *, now):
        self.result, self.completed_at = result, now


class Store:
    async def load_stream(self, *_args, **_kwargs):
        return ()

    async def append(self, stream, version, events, context):
        assert version == 0
        event = StoredEvent(
            **events[0].model_dump(),
            **context.model_dump(),
            event_id=UUID(int=900),
            position=1,
            stream_type=stream.stream_type,
            stream_id=stream.stream_id,
            stream_version=1,
            prev_hash="0" * 64,
            event_hash="0" * 64,
        )
        self.event = event.model_copy(update={"event_hash": calculate_event_hash(event)})
        return AppendResult(events=(self.event,), first_position=1, last_position=1)


class Snapshots:
    async def load(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("separate", [False, True])
async def test_historical_formal_clock_preserves_current_inbox_deadline(separate, monkeypatch):
    # Only the SQL boundary is substituted. The actual orchestrator decides,
    # appends, creates its outbox record and completes its inbox transaction.
    session, inbox, store = Session(), Inbox(), Store()
    current = datetime(2026, 9, 17, tzinfo=UTC)
    historical = current - timedelta(days=90)

    async def authorize(*_args):
        pass

    monkeypatch.setattr(
        "app.infrastructure.execution.sqlalchemy_orchestrator.configure_session_authorization",
        authorize,
    )
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=lambda: session,
        authorization=AuthorizationContext.system("execution-kernel"),
        aggregates={"run": RunAggregate()},
        inbox_factory=lambda _: inbox,
        event_store_factory=lambda _: store,
        snapshot_store_factory=lambda _: Snapshots(),
        now=lambda: current,
        **({"formal_now": lambda: historical} if separate else {}),
    )
    result = await handler.handle(next(_plan(1000).commands()).bind())
    assert result.status == "accepted"
    assert inbox.deadline == current + timedelta(seconds=30)
    assert inbox.claimed_at == inbox.completed_at == current
    assert store.event.occurred_at == (historical if separate else current)
    verify_stream((store.event,))
    assert session.committed
    assert len(session.rows) == 1
    assert session.rows[0].event_position == store.event.position
