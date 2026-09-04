"""Concurrent-writer safety for the formal projector's business-table writes.

The kernel projector and the API process both write execution-status columns on
the business tables (sessions.status/active_execution_run_id, patrol_runs.status,
...). Under last-writer-wins that races. C4a adds a ``last_event_position``
optimistic guard so the projector's writes are idempotent and monotonic: an event
whose position is at or below the row's recorded high-water mark can never regress
a newer state. These tests exercise that guard against a real PostgreSQL schema.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.domain.execution.events import StoredEvent
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_formal_projector import (
    PostgresFormalProjector as FormalProjector,
)
from tests.app.execution_test_support import (
    execution_admin_session,
    run_policy_snapshot_json,
)


def _event(
    *,
    run_id: UUID,
    owner_user_id: str,
    version: int,
    position: int,
    event_type: str,
    occurred_at: datetime,
    public_payload: dict,
    semantic_payload: dict | None = None,
) -> StoredEvent:
    """Build a run StoredEvent with an explicitly controlled log ``position``.

    Unlike the products fixture, position is decoupled from version so a test can
    model an out-of-order / concurrently-advanced business row.
    """
    internal_payload = (
        {"semantic_payload": semantic_payload} if semantic_payload is not None else {}
    )
    if event_type == "RunCreated":
        internal_payload["policy_snapshot"] = run_policy_snapshot_json(public_payload["family"])
    return StoredEvent(
        event_id=uuid4(),
        stream_type="run",
        stream_id=str(run_id),
        stream_version=version,
        position=position,
        event_type=event_type,
        event_schema_version=1,
        public_payload=public_payload,
        internal_payload=internal_payload,
        secret_ref=None,
        owner_user_id=owner_user_id,
        team_id=None,
        correlation_id=run_id,
        causation_id=None,
        occurred_at=occurred_at,
        prev_hash="0" * 64,
        event_hash=f"{version:x}" * 64,
    )


async def _apply_run_event(projector, session, event) -> None:
    """Per-event equivalent of the projector's batched fold (K4-4).

    ``_project_run`` was split into ``_track_run`` (in-memory fold) plus
    per-event product side effects and a final ``_flush_run`` UPSERT; these
    tests drive one event at a time, so each call folds and flushes.
    """
    trackers: dict = {}
    tracker = await projector._track_run(session, event, trackers)
    await projector._project_product_lifecycle(session, event, tracker.state)
    await projector._project_resource_build_failure(session, event, tracker.state)
    await projector._flush_run(session, tracker)


async def _session_row(session, session_id: str) -> object:
    return (
        await session.execute(
            text(
                "SELECT status, active_execution_run_id, last_event_position "
                "FROM sessions WHERE id = :id"
            ),
            {"id": session_id},
        )
    ).one()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_stale_run_event_cannot_regress_session_status() -> None:
    """An older-position run event must not roll a session back to an earlier state.

    Forward events advance ``last_event_position`` monotonically; once a concurrent
    writer has advanced the row past a given position, replaying/delivering an event
    at or below that position is a no-op for the guarded columns.
    """
    owner_user_id = f"competition-user-{uuid4()}"
    session_id = f"session-{uuid4()}"
    run_id = uuid4()
    occurred_at = datetime(2026, 8, 24, 15, tzinfo=UTC)
    projector = FormalProjector(
        session_factory=None,  # type: ignore[arg-type]
        authorization=AuthorizationContext.system("competition-test"),
    )

    async with execution_admin_session() as session:
        try:
            await session.execute(
                text("INSERT INTO users (id, email, username) VALUES (:id, :email, :username)"),
                {
                    "id": owner_user_id,
                    "email": f"{owner_user_id}@example.test",
                    "username": owner_user_id,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO sessions (id, owner_user_id, status) "
                    "VALUES (:id, :owner_user_id, '')"
                ),
                {"id": session_id, "owner_user_id": owner_user_id},
            )

            created = _event(
                run_id=run_id,
                owner_user_id=owner_user_id,
                version=1,
                position=100,
                event_type="RunCreated",
                occurred_at=occurred_at,
                public_payload={
                    "family": "agent",
                    "source_entity_type": "session",
                    "source_entity_id": session_id,
                    "parent_run_id": None,
                    "input": {},
                },
                semantic_payload={"session_id": session_id},
            )
            await _apply_run_event(projector, session, created)
            started = _event(
                run_id=run_id,
                owner_user_id=owner_user_id,
                version=2,
                position=200,
                event_type="RunStarted",
                occurred_at=occurred_at + timedelta(seconds=1),
                public_payload={},
            )
            await _apply_run_event(projector, session, started)
            waiting = _event(
                run_id=run_id,
                owner_user_id=owner_user_id,
                version=3,
                position=300,
                event_type="RunWaiting",
                occurred_at=occurred_at + timedelta(seconds=2),
                public_payload={"reason": "approval"},
            )
            await _apply_run_event(projector, session, waiting)

            # Forward progression advanced the guard monotonically.
            forward = await _session_row(session, session_id)
            assert forward.status == "waiting"
            assert forward.last_event_position == 300

            # A concurrent writer has already carried the session to a terminal
            # state at a far-higher position (e.g. the kernel processed the
            # RunCompleted out of band). The active run identity is cleared.
            await session.execute(
                text(
                    "UPDATE sessions SET status = 'completed', "
                    "active_execution_run_id = NULL, active_execution_request_id = NULL, "
                    "last_event_position = 9999 WHERE id = :id"
                ),
                {"id": session_id},
            )

            # Now a legitimately-evolvable but stale-positioned event arrives.
            # Without the guard this RunResumed would flip completed -> running and
            # re-attach the run. The last_event_position guard must block it.
            resumed = _event(
                run_id=run_id,
                owner_user_id=owner_user_id,
                version=4,
                position=400,
                event_type="RunResumed",
                occurred_at=occurred_at + timedelta(seconds=3),
                public_payload={},
            )
            await _apply_run_event(projector, session, resumed)

            blocked = await _session_row(session, session_id)
            assert blocked.status == "completed"
            assert blocked.active_execution_run_id is None
            assert blocked.last_event_position == 9999
        finally:
            await session.rollback()
            await session.execute(
                text("DELETE FROM execution_run_projection WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
            await session.execute(text("DELETE FROM sessions WHERE id = :id"), {"id": session_id})
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner_user_id})
            await session.commit()


async def _insert_patrol_run(
    session,
    *,
    owner_user_id: str,
    pack_id: str,
    patrol_run_id: str,
    execution_run_id: UUID,
) -> None:
    server_id = f"server-{pack_id}"
    await session.execute(
        text("INSERT INTO users (id, email, username) VALUES (:id, :email, :username)"),
        {
            "id": owner_user_id,
            "email": f"{owner_user_id}@example.test",
            "username": owner_user_id,
        },
    )
    await session.execute(
        text(
            "INSERT INTO mcp_servers (id, name, owner_user_id) VALUES (:id, :name, :owner_user_id)"
        ),
        {"id": server_id, "name": server_id, "owner_user_id": owner_user_id},
    )
    await session.execute(
        text(
            "INSERT INTO patrol_packs "
            "(id, owner_user_id, name, slug, status, config, mcp_server_id) VALUES "
            "(:id, :owner_user_id, :name, :slug, 'active', CAST(:config AS jsonb), :server_id)"
        ),
        {
            "id": pack_id,
            "owner_user_id": owner_user_id,
            "name": pack_id,
            "slug": pack_id,
            "config": json.dumps({"target_ref": "test", "checks": []}),
            "server_id": server_id,
        },
    )
    await session.execute(
        text(
            "INSERT INTO patrol_runs "
            "(id, pack_id, execution_run_id, pack_version, pack_snapshot, "
            "trigger_type, status, idempotency_key) VALUES "
            "(:id, :pack_id, :execution_run_id, 1, CAST(:snapshot AS jsonb), "
            "'manual', 'running', :idempotency_key)"
        ),
        {
            "id": patrol_run_id,
            "pack_id": pack_id,
            "execution_run_id": execution_run_id,
            "snapshot": json.dumps({"config": {"target_ref": "test", "checks": []}}),
            "idempotency_key": f"patrol:{patrol_run_id}",
        },
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_stale_terminal_event_cannot_regress_patrol_run_status() -> None:
    """A stale RunFailed must not fail a patrol run already advanced past its position.

    The projector's product-lifecycle failure write is guarded by both the status
    set (queued/running) and last_event_position. Here the status guard still
    matches (row is 'running'), so only the last_event_position guard can prevent
    the stale terminal event from regressing the durable product row.
    """
    owner_user_id = f"competition-patrol-{uuid4()}"
    pack_id = str(uuid4())
    patrol_run_id = str(uuid4())
    execution_run_id = uuid4()
    occurred_at = datetime(2026, 8, 24, 16, tzinfo=UTC)
    projector = FormalProjector(
        session_factory=None,  # type: ignore[arg-type]
        authorization=AuthorizationContext.system("competition-test"),
    )

    async with execution_admin_session() as session:
        try:
            await _insert_patrol_run(
                session,
                owner_user_id=owner_user_id,
                pack_id=pack_id,
                patrol_run_id=patrol_run_id,
                execution_run_id=execution_run_id,
            )
            await _apply_run_event(
                projector,
                session,
                _event(
                    run_id=execution_run_id,
                    owner_user_id=owner_user_id,
                    version=1,
                    position=100,
                    event_type="RunCreated",
                    occurred_at=occurred_at,
                    public_payload={
                        "family": "patrol",
                        "source_entity_type": "patrol_run",
                        "source_entity_id": patrol_run_id,
                        "parent_run_id": None,
                        "input": {},
                    },
                    semantic_payload={"patrol_run_id": patrol_run_id},
                ),
            )
            # A concurrent projector has already carried this product row past a
            # higher log position while it is still logically 'running'.
            await session.execute(
                text("UPDATE patrol_runs SET last_event_position = 9999 WHERE id = :id"),
                {"id": patrol_run_id},
            )

            # A stale terminal event at a lower position must not fail the run.
            await _apply_run_event(
                projector,
                session,
                _event(
                    run_id=execution_run_id,
                    owner_user_id=owner_user_id,
                    version=2,
                    position=200,
                    event_type="RunFailed",
                    occurred_at=occurred_at + timedelta(seconds=5),
                    public_payload={"failure_code": "PATROL_COLLECTOR_MISSING"},
                ),
            )
            product = (
                await session.execute(
                    text(
                        "SELECT status, finished_at, last_event_position "
                        "FROM patrol_runs WHERE id = :id"
                    ),
                    {"id": patrol_run_id},
                )
            ).one()

            assert product.status == "running"
            assert product.finished_at is None
            assert product.last_event_position == 9999
        finally:
            await session.rollback()
            await session.execute(
                text("DELETE FROM execution_run_projection WHERE run_id = :run_id"),
                {"run_id": execution_run_id},
            )
            await session.execute(
                text("DELETE FROM patrol_packs WHERE id = :pack_id"),
                {"pack_id": pack_id},
            )
            await session.execute(
                text("DELETE FROM mcp_servers WHERE id = :server_id"),
                {"server_id": f"server-{pack_id}"},
            )
            await session.execute(
                text("DELETE FROM users WHERE id = :owner_user_id"),
                {"owner_user_id": owner_user_id},
            )
            await session.commit()
