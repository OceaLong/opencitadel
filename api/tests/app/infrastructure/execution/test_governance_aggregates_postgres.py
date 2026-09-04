"""SQL governance aggregates must match the old in-memory reference (P2-17).

``approval_stats`` and ``governance_daily`` were rewritten from full-table
in-memory folds to SQL aggregation (COUNT/AVG, date_trunc + GROUP BY). These
tests seed a known row set against real PostgreSQL and check the SQL results
against the original Python algorithm applied to the same rows.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution import activity_types
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionActivityProjectionORM,
    ExecutionApprovalProjectionORM,
)
from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
)

# Far-future window so rows seeded by other integration tests never leak in.
SINCE = datetime(2030, 1, 1, tzinfo=UTC)


def _approval_row(
    owner: str,
    *,
    status: str,
    requested_at: datetime,
    decided_at: datetime | None,
) -> ExecutionApprovalProjectionORM:
    return ExecutionApprovalProjectionORM(
        approval_id=uuid4(),
        run_id=uuid4(),
        source_entity_type="session",
        source_entity_id="session-agg",
        approval_kind="tool_effect",
        subject_activity_id=uuid4(),
        subject_label="agg",
        risk_summary="aggregate check",
        status=status,
        decision=None if status == "pending" else status,
        decided_by_user_id=None,
        feedback="",
        owner_user_id=owner,
        team_id=None,
        request_event_position=1,
        decision_event_position=None,
        requested_at=requested_at,
        decided_at=decided_at,
    )


def _activity_row(
    owner: str,
    *,
    status: str,
    terminal_at: datetime,
    activity_type: str = activity_types.TOOL_CALL,
) -> ExecutionActivityProjectionORM:
    return ExecutionActivityProjectionORM(
        activity_id=uuid4(),
        run_id=uuid4(),
        activity_type=activity_type,
        status=status,
        attempt=1,
        generation=0,
        result_summary=None,
        failure_code="TOOL_ERROR" if status == "failed" else None,
        owner_user_id=owner,
        team_id=None,
        stream_version=1,
        last_event_position=1,
        state={"status": status},
        state_hash="0" * 64,
        created_at=terminal_at,
        updated_at=terminal_at,
        terminal_at=terminal_at,
    )


def _reference_approval_stats(rows: list[tuple[str, datetime, datetime | None]]) -> dict:
    """The pre-K4 in-memory algorithm, verbatim, as the comparison oracle."""
    outcomes = {"approved": 0, "rejected": 0, "cancelled": 0}
    durations: list[float] = []
    pending_count = 0
    for status, requested_at, decided_at in rows:
        if status == "pending":
            pending_count += 1
        elif status in outcomes:
            outcomes[status] += 1
        if decided_at is not None:
            durations.append((decided_at - requested_at).total_seconds())
    return {
        "pending_count": pending_count,
        "outcomes": outcomes,
        "avg_decision_seconds": (sum(durations) / len(durations) if durations else None),
    }


def _reference_governance_daily(
    approval_times: list[datetime],
    failure_times: list[datetime],
) -> list[dict]:
    daily: dict[str, dict[str, int]] = defaultdict(
        lambda: {"approval_requests": 0, "activity_failures": 0}
    )
    for occurred_at in approval_times:
        daily[occurred_at.date().isoformat()]["approval_requests"] += 1
    for occurred_at in failure_times:
        daily[occurred_at.date().isoformat()]["activity_failures"] += 1
    return [{"date": date, **counts} for date, counts in sorted(daily.items())]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_sql_aggregates_match_the_in_memory_reference() -> None:
    owner = f"agg-user-{uuid4()}"
    base = SINCE + timedelta(days=3, hours=6)
    approval_specs = [
        ("pending", base, None),
        ("pending", base + timedelta(hours=1), None),
        ("approved", base, base + timedelta(seconds=30)),
        ("approved", base + timedelta(days=1), base + timedelta(days=1, seconds=90)),
        ("rejected", base + timedelta(days=1, hours=2), base + timedelta(days=1, hours=3)),
        ("cancelled", base + timedelta(days=2), base + timedelta(days=2, seconds=5)),
        ("expired", base + timedelta(days=2, hours=1), base + timedelta(days=2, hours=2)),
    ]
    failure_times = [
        base + timedelta(hours=2),
        base + timedelta(days=1, hours=4),
        base + timedelta(days=1, hours=5),
    ]
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    projection = PostgresRunProjection(
        session_factory=sessions,
        authorization=AuthorizationContext.system("aggregate-reader"),
    )
    try:
        async with execution_admin_session() as session:
            for status, requested_at, decided_at in approval_specs:
                session.add(
                    _approval_row(
                        owner,
                        status=status,
                        requested_at=requested_at,
                        decided_at=decided_at,
                    )
                )
            for occurred_at in failure_times:
                session.add(_activity_row(owner, status="failed", terminal_at=occurred_at))
            session.add(
                _activity_row(
                    owner,
                    status="unknown",
                    terminal_at=base + timedelta(days=2, hours=3),
                )
            )
            # Noise that must NOT count: succeeded tool call, failed model call.
            session.add(_activity_row(owner, status="succeeded", terminal_at=base))
            session.add(
                _activity_row(
                    owner,
                    status="failed",
                    terminal_at=base,
                    activity_type=activity_types.MODEL_CALL,
                )
            )
            await session.commit()

        stats = await projection.approval_stats(SINCE)
        expected_stats = _reference_approval_stats(
            [
                (status, requested_at, decided_at)
                for status, requested_at, decided_at in approval_specs
            ]
        )
        assert stats["pending_count"] == expected_stats["pending_count"]
        assert stats["outcomes"] == expected_stats["outcomes"]
        assert stats["avg_decision_seconds"] == pytest.approx(
            expected_stats["avg_decision_seconds"]
        )

        daily = await projection.governance_daily(SINCE)
        expected_daily = _reference_governance_daily(
            [requested_at for _status, requested_at, _decided in approval_specs],
            [*failure_times, base + timedelta(days=2, hours=3)],
        )
        assert daily == expected_daily
    finally:
        await engine.dispose()
        async with execution_admin_session() as session:
            for table in (
                "execution_approval_projection",
                "execution_activity_projection",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE owner_user_id = :owner"),
                    {"owner": owner},
                )
            await session.commit()
