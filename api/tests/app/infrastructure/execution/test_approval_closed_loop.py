"""E3 approval closed-loop proof against real PostgreSQL.

Exercises the three halves the loop was missing:

* the formal projector, on ``ApprovalRequested``, pings the reviewer with an
  ``approval_waiting`` notification, and on ``ApprovalExpired`` with an
  ``approval_expired`` one (each exactly once, never on rebuild);
* an ``ApprovalExpired`` fact drives the approval projection to ``expired``
  (not ``cancelled``) even though a ``RunCancelled`` follows it; and
* the reviewer inbox query returns a scope's approvals, filtered by status and
  paginated, honouring owner scope.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.events import NewEvent
from app.domain.execution.store import AppendContext, StreamRef
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_formal_projector import (
    ApprovalWaitingNotice,
    PostgresApprovalNotifier,
    PostgresFormalProjector,
)
from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection
from app.infrastructure.repositories.db_notification_repository import DBNotificationRepository
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.notices: list[ApprovalWaitingNotice] = []

    async def approval_waiting(self, notice: ApprovalWaitingNotice) -> None:
        self.notices.append(notice)


def _context(owner_user_id: str) -> AppendContext:
    return AppendContext(
        owner_user_id=owner_user_id,
        team_id=None,
        correlation_id=uuid4(),
        causation_id=uuid4(),
        occurred_at=NOW,
    )


def _run_created(source_entity_id: str) -> NewEvent:
    return NewEvent(
        event_type="RunCreated",
        event_schema_version=2,
        public_payload={
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": source_entity_id,
            "parent_run_id": None,
            "input": {},
        },
        internal_payload={
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )


def _run_started() -> NewEvent:
    return NewEvent(
        event_type="RunStarted",
        event_schema_version=1,
        public_payload={},
        internal_payload={},
    )


def _approval_requested(approval_id: str, subject_label: str) -> NewEvent:
    return NewEvent(
        event_type="ApprovalRequested",
        event_schema_version=1,
        public_payload={
            "approval_id": approval_id,
            "subject_activity_id": str(uuid4()),
            "approval_kind": "tool_effect",
            "risk_summary": "Write to an external system",
            "subject_label": subject_label,
        },
        internal_payload={},
    )


async def _cleanup(owner_user_ids: list[str]) -> None:
    async with execution_admin_session() as session:
        for owner in owner_user_ids:
            for table, trigger in (
                ("execution_events", "execution_events_immutable"),
                ("execution_stream_owners", "execution_stream_owners_immutable"),
            ):
                await session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
                await session.execute(
                    text(f"DELETE FROM {table} WHERE owner_user_id = :owner"),
                    {"owner": owner},
                )
                await session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
            for table in (
                "execution_public_events",
                "execution_run_projection",
                "execution_approval_projection",
                "execution_projector_checkpoints",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE owner_user_id = :owner"),
                    {"owner": owner},
                )
            await session.execute(
                text("DELETE FROM notifications WHERE user_id = :owner"),
                {"owner": owner},
            )
            await session.execute(
                text("DELETE FROM execution_scope_head WHERE owner_scope_key = :key"),
                {"key": f"user:{owner}"},
            )
            await session.execute(
                text("DELETE FROM users WHERE id = :owner"),
                {"owner": owner},
            )
        await session.commit()


async def _seed_user(owner: str) -> None:
    async with execution_admin_session() as session:
        await session.execute(
            text("INSERT INTO users (id, email, username) VALUES (:id, :email, :username)"),
            {"id": owner, "email": f"{owner}@example.test", "username": owner},
        )
        await session.commit()


@pytest.fixture
async def kernel_factory(_db_schema):
    engine = create_async_engine(execution_kernel_database_uri())
    factory = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approval_request_pings_reviewer_and_expiry_marks_projection_expired(
    kernel_factory,
) -> None:
    owner = f"approval-user-{uuid4()}"
    scope = OwnerScope.personal(owner)
    run_id = uuid4()
    approval_id = uuid4()
    stream = StreamRef(stream_type="run", stream_id=str(run_id))
    notifier = _RecordingNotifier()
    projector = PostgresFormalProjector(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("approval-projector"),
        notifier=notifier,
    )
    projection = PostgresRunProjection(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("approval-reader"),
    )

    try:
        async with kernel_factory() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("approval-append")
            )
            await PostgresEventStore(session).append(
                stream,
                0,
                (
                    _run_created("session-1"),
                    _run_started(),
                    _approval_requested(str(approval_id), "write_external"),
                ),
                _context(owner),
            )
            await session.commit()

        # ApprovalRequested -> the reviewer is pinged exactly once, and the
        # approval is projected as pending.
        first = await projector.run_once(scope, limit=100)
        assert first.processed == 3
        assert len(notifier.notices) == 1
        notice = notifier.notices[0]
        assert notice.user_id == owner
        assert notice.approval_id == approval_id
        assert notice.run_id == run_id
        assert notice.session_id == "session-1"
        assert notice.subject_label == "write_external"

        pending = await projection.list_approvals(
            owner_scope=scope, status="pending", limit=50, offset=0
        )
        assert [entry.approval_id for entry in pending] == [approval_id]

        # The approval times out: ApprovalExpired then RunCancelled.
        async with kernel_factory() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("approval-append")
            )
            await PostgresEventStore(session).append(
                stream,
                3,
                (
                    NewEvent(
                        event_type="ApprovalExpired",
                        event_schema_version=1,
                        public_payload={"approval_id": str(approval_id)},
                        internal_payload={},
                    ),
                    NewEvent(
                        event_type="RunCancelled",
                        event_schema_version=1,
                        public_payload={"reason": "approval_expired"},
                        internal_payload={},
                    ),
                ),
                _context(owner),
            )
            await session.commit()

        second = await projector.run_once(scope, limit=100)
        assert second.processed == 2
        # The expiry itself pings once more so the initiator learns the Run was
        # cancelled by timeout, instead of it failing silently.
        assert len(notifier.notices) == 2
        assert notifier.notices[1].kind == "approval_expired"
        assert notifier.notices[1].user_id == owner

        # The approval settled as 'expired', not 'cancelled', despite the
        # trailing RunCancelled.
        assert (
            await projection.list_approvals(owner_scope=scope, status="pending", limit=50, offset=0)
            == ()
        )
        expired = await projection.list_approvals(
            owner_scope=scope, status="expired", limit=50, offset=0
        )
        assert [entry.approval_id for entry in expired] == [approval_id]
        assert expired[0].status == "expired"
        assert expired[0].decision == "expired"

        # Rebuild replays every event but must NOT re-ping the reviewer.
        await projector.rebuild(scope)
        assert len(notifier.notices) == 2
    finally:
        await _cleanup([owner])


@pytest.mark.asyncio
async def test_postgres_notifier_persists_an_approval_waiting_notification(
    kernel_factory,
) -> None:
    owner = f"notify-user-{uuid4()}"
    run_id = uuid4()
    approval_id = uuid4()
    stream = StreamRef(stream_type="run", stream_id=str(run_id))
    notifier = PostgresApprovalNotifier(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("approval-notifier"),
        publisher=None,
    )
    projector = PostgresFormalProjector(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("approval-projector"),
        notifier=notifier,
    )
    try:
        await _seed_user(owner)
        async with kernel_factory() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("approval-append")
            )
            await PostgresEventStore(session).append(
                stream,
                0,
                (
                    _run_created("session-notify"),
                    _run_started(),
                    _approval_requested(str(approval_id), "delete_prod"),
                ),
                _context(owner),
            )
            await session.commit()

        await projector.run_once(scope := OwnerScope.personal(owner), limit=100)
        assert scope.user_id == owner

        # The reviewer really has a durable approval_waiting notification.
        async with kernel_factory() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("approval-notify-read")
            )
            rows = await DBNotificationRepository(session).list_for_user(owner)
        assert len(rows) == 1
        assert rows[0].type == "approval_waiting"
        assert rows[0].user_id == owner
    finally:
        await _cleanup([owner])


@pytest.mark.asyncio
async def test_inbox_lists_own_pending_approvals_scoped_and_paginated(
    kernel_factory,
) -> None:
    mine = f"inbox-mine-{uuid4()}"
    other = f"inbox-other-{uuid4()}"
    mine_scope = OwnerScope.personal(mine)
    projector = PostgresFormalProjector(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("inbox-projector"),
    )
    projection = PostgresRunProjection(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("inbox-reader"),
    )

    async def _seed(owner: str, subject: str) -> None:
        run_id = uuid4()
        stream = StreamRef(stream_type="run", stream_id=str(run_id))
        async with kernel_factory() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("inbox-append")
            )
            await PostgresEventStore(session).append(
                stream,
                0,
                (
                    _run_created(f"session-{subject}"),
                    _run_started(),
                    _approval_requested(str(uuid4()), subject),
                ),
                _context(owner),
            )
            await session.commit()

    try:
        await _seed(mine, "mine-a")
        await _seed(mine, "mine-b")
        await _seed(other, "other-a")

        await projector.run_once(mine_scope, limit=100)
        await projector.run_once(OwnerScope.personal(other), limit=100)

        # Only my scope's approvals are visible.
        page_all = await projection.list_approvals(
            owner_scope=mine_scope, status="pending", limit=50, offset=0
        )
        assert len(page_all) == 2
        assert {entry.subject_label for entry in page_all} == {"mine-a", "mine-b"}

        # Pagination: a limit of 1 returns one row, and the offset returns the
        # other, with no overlap.
        first = await projection.list_approvals(
            owner_scope=mine_scope, status="pending", limit=1, offset=0
        )
        second = await projection.list_approvals(
            owner_scope=mine_scope, status="pending", limit=1, offset=1
        )
        assert len(first) == 1
        assert len(second) == 1
        assert first[0].approval_id != second[0].approval_id
    finally:
        await _cleanup([mine, other])
