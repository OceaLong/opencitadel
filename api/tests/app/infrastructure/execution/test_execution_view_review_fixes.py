from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.postgres_playback import load_playback
from tests.app.execution_test_support import execution_admin_session
from tests.app.infrastructure.execution.test_postgres_execution_view import (
    repository,
    service,
    write,
)

pytestmark = pytest.mark.usefixtures("postgres_integration")


async def large_run():
    run = uuid4()
    scope = OwnerScope.personal("f04-fix-" + str(uuid4()))
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    import json

    facts = [
        {"kind": "step", "id": f"s-{i:05}", "patch": {"kind": "tool", "status": "running"}}
        for i in range(10000)
    ]
    async with execution_admin_session() as session:
        await session.execute(
            text("""INSERT INTO execution_view_observations(run_id,observed_order,source_kind,source_identity,formal_position,progress_position,observed_at,projection_revision,projector_version,public_payload,owner_user_id,created_by)
            VALUES(:run,2,'formal','large',2,0,:now,2,1,CAST(:payload AS jsonb),:owner,'f04-fix')"""),
            {
                "run": run,
                "owner": scope.user_id,
                "now": datetime.now(UTC),
                "payload": json.dumps({"facts": facts}),
            },
        )
        await session.execute(
            text(
                "UPDATE execution_view_runs SET observed_order=2,projection_revision=2,formal_position=2,as_of=(SELECT observed_at FROM execution_view_observations WHERE run_id=:run AND observed_order=2) WHERE run_id=:run"
            ),
            {"run": run},
        )
        await session.commit()
    await repository().rebuild_scope_shadow(scope)
    return scope, run


@pytest.mark.asyncio
async def test_large_current_page_point_and_run_summary_do_not_build_all_steps(monkeypatch):
    import app.application.services.execution_view_service as module

    scope, run = await large_run()
    api = service()
    from contextlib import asynccontextmanager

    queries = []
    original_transaction = api.port.transaction

    @asynccontextmanager
    async def tracked_transaction(**kwargs):
        async with original_transaction(**kwargs) as session:
            connection = await session.connection()

            def capture(conn, cursor, statement, parameters, context, executemany):
                queries.append(statement)

            event.listen(connection.sync_connection, "before_cursor_execute", capture)
            yield session

    monkeypatch.setattr(api.port, "transaction", tracked_transaction)
    built = []
    original = module.StepView

    def counted(**kwargs):
        built.append(kwargs["step_id"])
        return original(**kwargs)

    counted.model_fields = original.model_fields
    monkeypatch.setattr(module, "StepView", counted)
    page = await api.list_steps(scope, run, limit=1)
    assert len(page.items) == 1
    assert len(built) <= 2
    built.clear()
    assert (await api.get_step(scope, run, "s-00001")).step_id == "s-00001"
    assert len(built) == 1
    built.clear()
    assert len((await api.list_runs(scope)).items) == 1
    assert built == []
    step_reads = [q for q in queries if "SELECT step_id,observed_order,payload" in q]
    assert len(step_reads) == 2
    assert all(
        "execution_view_shadow_steps" in q and "scope_key" in q and "LIMIT" in q for q in step_reads
    )
    assert not any("jsonb_array_elements" in q for q in queries)
    assert not any(
        "SELECT state,missing_intervals FROM execution_view_shadow_runs" in q for q in queries
    )


@pytest.mark.asyncio
async def test_active_shadow_and_later_write_follow_f03_when_retention_changes():
    scope = OwnerScope.personal("f04-fix-" + str(uuid4()))
    run = uuid4()
    repo = repository()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    await write(
        run,
        scope,
        2,
        {"role": "assistant", "public_summary": "removed-message"},
        kind="message",
        identity="m",
    )
    await write(run, scope, 3, {"phase": "now"}, kind="step", identity="s", source_kind="progress")
    await repo.rebuild_scope_shadow(scope)
    async with execution_admin_session() as session:
        # Deliberately unavailable isolated journal fixture, not production cleanup.
        await session.execute(
            text("DELETE FROM execution_view_observations WHERE run_id=:run AND observed_order=2"),
            {"run": run},
        )
        await session.commit()
    for later in (False, True):
        if later:
            await write(
                run, scope, 4, {"progress": 50}, kind="step", identity="s", source_kind="progress"
            )
        async with repo.transaction() as session:
            cut = await repo.capture_run(session, scope, run)
            expected = await load_playback(session, cut, trusted_scope=scope)
            actual = await repo.restore(
                session, scope, cut, await repo.active_generation(session, scope)
            )
            assert actual.state == expected.state
            assert actual.missing_intervals == expected.missing_intervals
        view = await service().get_view(scope, run)
        assert view.messages == []
        assert any(
            i.reason == "journal_observation_gap" for i in view.run.completeness.missing_intervals
        )


@pytest.mark.asyncio
async def test_missing_origin_checkpoint_does_not_advertise_unusable_first_cursor():
    scope = OwnerScope.personal("f04-fix-" + str(uuid4()))
    run = uuid4()
    repo = repository()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    await write(run, scope, 2, {"status": "completed"})
    async with execution_admin_session() as session:
        await session.execute(
            text("DELETE FROM execution_view_observations WHERE run_id=:run AND observed_order=1"),
            {"run": run},
        )
        await session.commit()
    async with repo.transaction() as session:
        cut = await repo.capture_run(session, scope, run)
        assert await repo.first_replayable(session, scope, cut) is None


@pytest.mark.asyncio
async def test_warm_historical_pages_use_scoped_sql_cache_not_payload_replay(monkeypatch):
    from contextlib import asynccontextmanager

    scope, run = await large_run()
    api = service()
    selected = await api.list_steps(scope, run, limit=1)
    await write(
        run, scope, 3, {"progress": 30}, kind="step", identity="s-00000", source_kind="progress"
    )
    first = await api.list_steps(scope, run, at=selected.at, limit=1)
    queries = []
    original = api.port.transaction

    @asynccontextmanager
    async def transaction(**kwargs):
        async with original(**kwargs) as session:
            conn = await session.connection()

            def capture(conn, cursor, statement, parameters, context, executemany):
                queries.append(statement)

            event.listen(conn.sync_connection, "before_cursor_execute", capture)
            yield session

    monkeypatch.setattr(api.port, "transaction", transaction)
    second = await api.list_steps(scope, run, at=first.at, cursor=first.next_cursor, limit=1)
    assert second.items[0].step_id == "s-09998"
    assert not any("jsonb_array_elements" in q for q in queries)
    step_reads = [q for q in queries if "SELECT step_id,observed_order,payload" in q]
    assert len(step_reads) == 1
    assert all(
        x in step_reads[0]
        for x in (
            "execution_view_read_steps",
            "scope_key",
            "ORDER BY",
            "LIMIT",
            "observed_order,step_id",
        )
    )
    assert not any(
        "SELECT state,missing_intervals FROM execution_view_shadow_runs" in q for q in queries
    )


@pytest.mark.asyncio
async def test_coverage_metadata_change_invalidates_shadow_and_historical_cache():
    import json

    scope = OwnerScope.personal("f04-fix-" + str(uuid4()))
    run = uuid4()
    api = service()
    repo = repository()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    await write(run, scope, 2, {"progress": 5}, kind="step", identity="s", source_kind="progress")
    await api.get_view(scope, run)
    await repo.rebuild_scope_shadow(scope)
    await api.get_view(scope, run)
    interval = {"start": None, "end": None, "reason": "retained_text_unavailable"}
    async with execution_admin_session() as session:
        await session.execute(
            text(
                "UPDATE execution_view_runs SET completeness=jsonb_set(completeness,'{missing_intervals}',CAST(:intervals AS jsonb)) WHERE run_id=:run"
            ),
            {"run": run, "intervals": json.dumps([interval])},
        )
        await session.commit()
    for _ in range(2):
        view = await api.get_view(scope, run)
        assert any(i.reason == interval["reason"] for i in view.run.completeness.missing_intervals)
    await write(run, scope, 3, {"progress": 10}, kind="step", identity="s", source_kind="progress")
    async with repo.transaction() as session:
        cut = await repo.capture_run(session, scope, run)
        expected = await load_playback(session, cut, trusted_scope=scope)
        actual = await repo.restore(
            session, scope, cut, await repo.active_generation(session, scope)
        )
        assert actual.state == expected.state
        assert actual.missing_intervals == expected.missing_intervals


@pytest.mark.asyncio
async def test_usable_later_state_reports_null_first_cursor_with_prefix_gap():
    scope = OwnerScope.personal("f04-fix-" + str(uuid4()))
    run = uuid4()
    api = service()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    await write(run, scope, 2, {"status": "completed"})
    await write(run, scope, 3, {"family": "agent", "status": "completed"})
    async with execution_admin_session() as session:
        await session.execute(
            text("DELETE FROM execution_view_observations WHERE run_id=:run AND observed_order=1"),
            {"run": run},
        )
        await session.commit()
    view = await api.get_view(scope, run)
    assert view.run.first_replayable_cursor is None
    assert any(
        i.reason == "journal_observation_gap" for i in view.run.completeness.missing_intervals
    )


@pytest.mark.asyncio
async def test_cache_scope_grants_expiry_and_bounded_cleanup():
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.application.services.execution_view_service import ExecutionViewService
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import Principal
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    scope = OwnerScope.personal("f04-fix-" + str(uuid4()))
    run = uuid4()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    factory = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    auth = AuthorizationContext.for_principal(Principal(user_id=scope.user_id))
    try:
        api = ExecutionViewService(
            PostgresExecutionView(session_factory=factory, authorization=auth),
            cursor_secret=b"1234567890123456",
        )
        await api.get_view(scope, run)
        async with factory() as session:
            await configure_session_authorization(session, auth)
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM execution_view_read_cuts WHERE run_id=:run"),
                    {"run": run},
                )
                == 1
            )
            with pytest.raises(DBAPIError):
                await session.execute(text("UPDATE execution_view_read_cuts SET state='{}'::jsonb"))
            await session.rollback()
        async with factory() as session:
            await configure_session_authorization(
                session, AuthorizationContext.for_principal(Principal(user_id="foreign"))
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM execution_view_read_cuts WHERE run_id=:run"),
                    {"run": run},
                )
                == 0
            )
        async with execution_admin_session() as session:
            await session.execute(
                text(
                    "UPDATE execution_view_read_cuts SET created_at=CURRENT_TIMESTAMP-interval '20 minutes',expires_at=CURRENT_TIMESTAMP-interval '5 minutes' WHERE run_id=:run"
                ),
                {"run": run},
            )
            await session.commit()
        assert await repository().cleanup_expired(limit=1) >= 1
        await api.get_view(scope, run)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_key_update_serializes_formal_and_progress_on_same_run():
    import asyncio

    from app.application.execution.view_facts import ProjectionFact
    from app.infrastructure.execution.postgres_view_observations import observe

    scope = OwnerScope.personal("f04-fix-" + str(uuid4()))
    run = uuid4()
    async with execution_admin_session() as first:
        await observe(
            first,
            run_id=run,
            owner_user_id=scope.user_id,
            team_id=None,
            family="agent",
            fact=ProjectionFact(
                10, 0, None, "run", str(run), {"family": "agent", "status": "running"}, "formal"
            ),
            source_identity="formal",
            event_id=None,
            occurred_at=datetime.now(UTC),
        )
        entered = asyncio.Event()

        async def later():
            entered.set()
            return await write(
                run, scope, 0, {"progress": 25}, kind="step", identity="s", source_kind="progress"
            )

        task = asyncio.create_task(later())
        await entered.wait()
        await asyncio.sleep(0.1)
        assert not task.done()
        await first.commit()
        result = await asyncio.wait_for(task, 5)
        assert result.observed_order == 2
        assert result.formal_position == 10
        assert result.progress_position == 1


@pytest.mark.asyncio
async def test_sql_unknown_filters_match_progress_only_step_defaults():
    scope = OwnerScope.personal("f04-fix-" + str(uuid4()))
    run = uuid4()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    await write(
        run, scope, 2, {"progress": 20}, kind="step", identity="unstarted", source_kind="progress"
    )
    page = await service().list_steps(scope, run, filters={"kind": "unknown", "status": "unknown"})
    assert [s.step_id for s in page.items] == ["unstarted"]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_gap", [False, True], ids=["intact-prefix", "retained-gap"])
async def test_incremental_boundary_includes_newly_relevant_coverage_and_preserves_gaps(with_gap):
    import json

    scope = OwnerScope.personal("f04-fix2-" + str(uuid4()))
    run = uuid4()
    repo = repository()
    await write(
        run,
        scope,
        1,
        {
            "family": "agent",
            "status": "running",
            "completeness": {"state": "complete", "missing_fields": [], "missing_intervals": []},
        },
    )
    await write(
        run,
        scope,
        2,
        {"role": "assistant", "public_summary": "retained"},
        kind="message",
        identity="m",
    )
    await write(run, scope, 3, {"progress": 5}, kind="step", identity="s", source_kind="progress")
    await repo.rebuild_scope_shadow(scope)
    if with_gap:
        async with execution_admin_session() as session:
            await session.execute(
                text(
                    "DELETE FROM execution_view_observations WHERE run_id=:run AND observed_order=2"
                ),
                {"run": run},
            )
            await session.commit()
        # Establish a valid cached state that already contains a computed gap.
        await write(
            run, scope, 4, {"progress": 10}, kind="step", identity="s", source_kind="progress"
        )
    async with repo.transaction() as session:
        previous = await repo.capture_run(session, scope, run)
    start = datetime.now(UTC)
    assert start > previous.observed_at
    interval = {"start": start.isoformat(), "end": None, "reason": "newly_relevant_text_gap"}
    async with execution_admin_session() as session:
        await session.execute(
            text(
                "UPDATE execution_view_runs SET completeness=jsonb_set(completeness,'{missing_intervals}',CAST(:intervals AS jsonb)) WHERE run_id=:run"
            ),
            {"run": run, "intervals": json.dumps([interval])},
        )
        await session.commit()
    # T1 is excluded at T0, so the previous shadow stays valid.
    async with repo.transaction() as session:
        before = await repo.restore(
            session, scope, previous, await repo.active_generation(session, scope)
        )
        assert not any(i["reason"] == interval["reason"] for i in before.missing_intervals)
    for progress in (20, 30):
        await write(
            run, scope, 5, {"progress": progress}, kind="step", identity="s", source_kind="progress"
        )
        async with repo.transaction() as session:
            cut = await repo.capture_run(session, scope, run)
            assert cut.observed_at >= start
            expected = await load_playback(session, cut, trusted_scope=scope)
            actual = await repo.restore(
                session, scope, cut, await repo.active_generation(session, scope)
            )
            assert actual.state == expected.state
            assert actual.missing_intervals == expected.missing_intervals
            if with_gap:
                assert any(
                    i.get("start_order") == 2 and i.get("end_order") == 2
                    for i in actual.missing_intervals
                )
        view = await service().get_view(scope, run)
        assert any(i.reason == interval["reason"] for i in view.run.completeness.missing_intervals)
