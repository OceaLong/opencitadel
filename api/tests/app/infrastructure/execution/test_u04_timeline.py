from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.application.ports.execution_view import ViewCursorInvalid, ViewRevisionExpired
from app.domain.models.scope import OwnerScope
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.execution_test_support import execution_admin_session
from tests.app.infrastructure.execution.test_postgres_execution_view import service, write

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_full_datetime_bounds_keep_finite_buckets_and_exact_adjacency():
    scope, run = OwnerScope.personal("u07-" + str(uuid4())), uuid4()
    await write(run, scope, 1, {"family": "agent", "status": "queued"})
    await write(run, scope, 2, {"status": "running"})
    api = service()
    live = await api.get_view(scope, run)
    result = await api.get_timeline(
        scope,
        run,
        datetime.min.replace(tzinfo=UTC),
        datetime.max.replace(tzinfo=UTC),
        anchor_at=live.at,
    )
    assert sum(bucket.count for bucket in result.buckets) == 2
    assert len(result.key_events) == 2
    assert api._decode_at(scope, run, result.at)[0].observed_order == 1
    assert (await api.get_view(scope, run, at=result.at)).run.status == "queued"


async def test_adjacent_formal_keys_cross_200_ties_and_preserve_opaque_authority():
    scope, run = OwnerScope.personal("u04-" + str(uuid4())), uuid4()
    for position in range(1, 204):
        last = await write(run, scope, position, {"family": "agent", "status": "running"})
    now = last.observed_at
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE execution_view_observations SET observed_at=:at WHERE run_id=:run"),
            {"at": now, "run": run},
        )
        await db.commit()
    api = service()
    start, end = now - timedelta(seconds=1), now + timedelta(seconds=1)
    first = await api.get_timeline(scope, run, start, end, now, "after")
    assert len(first.key_events) == 200
    current = first.at
    for expected in range(2, 204):
        result = await api.get_timeline(
            scope, run, start, end, direction="after", anchor_at=current
        )
        assert result.at
        assert result.at != current
        assert api._decode_at(scope, run, result.at)[0].observed_order == expected
        current = result.at
    assert (
        await api.get_timeline(scope, run, start, end, direction="after", anchor_at=current)
    ).at is None
    previous = await api.get_timeline(scope, run, start, end, anchor_at=current)
    assert api._decode_at(scope, run, previous.at)[0].observed_order == 202
    assert (await api.get_timeline(scope, run, start, end, anchor_at=first.at)).at is None
    with pytest.raises(ViewCursorInvalid):
        await api.get_timeline(scope, run, start, end, now, anchor_at=current)
    with pytest.raises(ViewCursorInvalid):
        await api.get_timeline(OwnerScope.personal("other"), run, start, end, anchor_at=current)
    with pytest.raises(ViewCursorInvalid):
        await api.get_timeline(scope, uuid4(), start, end, anchor_at=current)
    boundary, _ = api._decode_at(scope, run, current)
    with pytest.raises(ViewRevisionExpired):
        await api.get_timeline(
            scope, run, start, end, anchor_at=api._at(scope, boundary, str(uuid4()))
        )


async def test_adjacency_validates_persisted_anchor_and_exposes_retention_coverage():
    scope, run = OwnerScope.personal("u04-" + str(uuid4())), uuid4()
    for position in range(1, 4):
        last = await write(run, scope, position, {"family": "agent", "status": "running"})
    api = service()
    start, end = last.observed_at - timedelta(days=1), last.observed_at + timedelta(days=1)
    live = await api.get_view(scope, run)
    boundary, generation = api._decode_at(scope, run, live.at)
    altered = boundary.model_copy(
        update={"observed_at": boundary.observed_at + timedelta(seconds=1)}
    )
    with pytest.raises(ViewRevisionExpired):
        await api.get_timeline(
            scope, run, start, end, anchor_at=api._at(scope, altered, generation)
        )
    previous = await api.get_timeline(scope, run, start, end, anchor_at=live.at)
    async with execution_admin_session() as db:
        await db.execute(
            text("DELETE FROM execution_view_observations WHERE run_id=:run AND observed_order=2"),
            {"run": run},
        )
        await db.commit()
    with pytest.raises(ViewRevisionExpired):
        await api.get_timeline(scope, run, start, end, anchor_at=previous.at)
    retained = await api.get_timeline(scope, run, start, end, anchor_at=live.at)
    assert retained.completeness.state == "partial"
    assert any(
        g.reason == "journal_observation_gap" for g in retained.completeness.missing_intervals
    )
    assert api._decode_at(scope, run, retained.at)[0].observed_order == 1


async def test_original_time_resolution_and_bucket_contract_remain_compatible():
    from tests.app.infrastructure.execution.test_postgres_execution_view import (
        test_timeline_exact_tied_boundaries_and_server_buckets,
    )

    await test_timeline_exact_tied_boundaries_and_server_buckets()
