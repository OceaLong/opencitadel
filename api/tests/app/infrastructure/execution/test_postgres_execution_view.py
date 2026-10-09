from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.application.execution.view_facts import ProjectionFact
from app.application.services.execution_view_service import ExecutionViewService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
from app.infrastructure.execution.postgres_view_observations import observe
from tests.app.execution_test_support import execution_admin_session

pytestmark = pytest.mark.usefixtures("postgres_integration")


@pytest.mark.asyncio
async def test_progress_staleness_materializes_only_latest_effective_source():
    run, activity = uuid4(), uuid4()
    async with execution_admin_session() as session:
        queries = []
        connection = await session.connection()

        def capture(conn, cursor, statement, parameters, context, executemany):
            if "SELECT" in statement and "public_payload" in statement and "->>" in statement:
                queries.append(statement)

        event.listen(connection.sync_connection, "before_cursor_execute", capture)
        for seq in (2, 1, 3):
            await observe(
                session,
                run_id=run,
                owner_user_id="f04-progress",
                team_id=None,
                family="agent",
                fact=ProjectionFact(0, 0, None, "step", "s", {"progress": seq}, "progress"),
                source_identity=str(seq),
                event_id=None,
                occurred_at=datetime.now(UTC),
                source={
                    "activity_id": str(activity),
                    "generation": 0,
                    "claim_generation": 1,
                    "sequence": seq,
                },
            )
        assert len(queries) == 3
        assert all("LIMIT" in q and "ORDER BY" in q for q in queries)
        assert all("coalesce" in q.lower() for q in queries)
        await session.rollback()


def repository():
    return PostgresExecutionView(
        session_factory=execution_admin_session, authorization=AuthorizationContext.system("f04")
    )


def service():
    return ExecutionViewService(repository(), cursor_secret=b"f04-cursor-secret-is-long-enough")


async def write(run, scope, position, patch, *, kind="run", identity=None, source_kind="formal"):
    async with execution_admin_session() as session:
        result = await observe(
            session,
            run_id=run,
            owner_user_id=scope.user_id if scope.team_id is None else None,
            team_id=scope.team_id,
            family="agent",
            fact=ProjectionFact(position, 0, None, kind, identity or str(run), patch, source_kind),
            source_identity=str(uuid4()),
            event_id=None,
            occurred_at=datetime.now(UTC),
        )
        await session.commit()
        return result


@pytest.mark.asyncio
async def test_run_cohort_pages_freeze_membership_state_and_tied_identity():
    scope = OwnerScope.personal("f04-" + str(uuid4()))
    other = OwnerScope.personal("f04-" + str(uuid4()))
    runs = sorted([uuid4() for _ in range(3)], reverse=True)
    now = datetime.now(UTC).isoformat()
    for run in runs:
        await write(run, scope, 1, {"family": "agent", "status": "running", "admitted_at": now})
    await write(uuid4(), other, 1, {"family": "agent", "status": "running", "admitted_at": now})
    api = service()
    first = await api.list_runs(scope, {"state": "running"}, limit=1)
    assert [r.run_id for r in first.items] == runs[:1]
    await write(runs[1], scope, 2, {"status": "completed"})
    await write(uuid4(), scope, 3, {"family": "agent", "status": "running", "admitted_at": now})
    second = await api.list_runs(scope, {"state": "running"}, cursor=first.next_cursor, limit=1)
    third = await api.list_runs(scope, {"state": "running"}, cursor=second.next_cursor, limit=1)
    assert [r.run_id for r in second.items + third.items] == runs[1:]
    assert second.items[0].status == "running"
    assert third.next_cursor is None


@pytest.mark.asyncio
async def test_step_pages_remain_historical_after_live_changes_and_scope_absence():
    from app.application.ports.execution_view import ViewCursorInvalid, ViewNotFound

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    for name in ("a", "b", "c"):
        await write(
            run,
            scope,
            2,
            {"kind": "tool", "status": "running", "activity_id": str(uuid4())},
            kind="step",
            identity=name,
        )
    api = service()
    page = await api.list_steps(scope, run, limit=1)
    assert [s.step_id for s in page.items] == ["c"]
    await write(run, scope, 3, {"progress": 80}, kind="step", identity="a", source_kind="progress")
    await write(
        run,
        scope,
        4,
        {"kind": "tool", "status": "queued", "activity_id": str(uuid4())},
        kind="step",
        identity="d",
    )
    second = await api.list_steps(
        scope, run, revision=page.revision, at=page.at, cursor=page.next_cursor, limit=10
    )
    assert [s.step_id for s in second.items] == ["b", "a"]
    assert all(s.projection_revision == page.revision for s in second.items)
    assert second.items[1].progress is None
    with pytest.raises(ViewNotFound):
        await api.get_view(OwnerScope.personal("nobody"), run)
    with pytest.raises(ViewCursorInvalid):
        await api.list_steps(scope, run, filters={"kind": "model"}, cursor=page.next_cursor)
    latest = await api.get_step(scope, run, "a")
    assert latest.progress == 80
    assert latest.status == "running"


@pytest.mark.asyncio
async def test_shadow_generation_activation_and_normal_writer_freshness():
    from app.application.ports.execution_view import ViewRevisionExpired

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    repo = repository()
    api = service()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    before = await api.get_view(scope, run)
    built = await repo.rebuild_scope_shadow(scope)
    assert built.activated
    assert built.generation
    assert built.source_version == 1
    after = await api.get_view(scope, run)
    assert after.run.status == "running"
    assert after.revision == before.revision
    with pytest.raises(ViewRevisionExpired):
        await api.get_view(scope, run, at=before.at)
    await write(run, scope, 2, {"status": "completed"})
    assert (await api.get_view(scope, run)).run.status == "completed"
    again = await repo.rebuild_scope_shadow(scope)
    assert again.generation != built.generation
    assert again.algorithm_version == built.algorithm_version


@pytest.mark.asyncio
async def test_active_shadow_progress_updates_without_replaying_history():
    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    await repository().rebuild_scope_shadow(scope)
    async with execution_admin_session() as session:
        queries = []
        conn = await session.connection()

        def capture(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        event.listen(conn.sync_connection, "before_cursor_execute", capture)
        await observe(
            session,
            run_id=run,
            owner_user_id=scope.user_id,
            team_id=None,
            family="agent",
            fact=ProjectionFact(0, 0, None, "step", "attempt", {"progress": 50}, "progress"),
            source_identity="progress",
            event_id=None,
            occurred_at=datetime.now(UTC),
        )
        assert not any(
            "SELECT" in q
            and "execution_view_checkpoints" in q
            and "state_ref->'missing_intervals'" not in q
            for q in queries
        )
        assert not any("jsonb_array_elements" in q for q in queries)
        await session.commit()
    assert (await service().get_step(scope, run, "attempt")).progress == 50


@pytest.mark.asyncio
async def test_timeline_exact_tied_boundaries_and_server_buckets():
    from datetime import timedelta

    from sqlalchemy import text

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    await write(run, scope, 1, {"family": "agent", "status": "queued"})
    second = await write(run, scope, 2, {"status": "running"})
    now = second.observed_at
    async with execution_admin_session() as session:
        # Controlled fixture tie; no original execution facts are mutated.
        await session.execute(
            text("UPDATE execution_view_observations SET observed_at=:at WHERE run_id=:run"),
            {"at": now, "run": run},
        )
        await session.commit()
    api = service()
    before = await api.get_timeline(
        scope, run, now - timedelta(seconds=1), now + timedelta(seconds=1), now, "before", 2
    )
    after = await api.get_timeline(
        scope, run, now - timedelta(seconds=1), now + timedelta(seconds=1), now, "after", 2
    )
    assert sum(b.count for b in before.buckets) == 2
    assert len(before.key_events) == 2
    assert before.key_events[-1].kinds == ["run"]
    assert (await api.get_view(scope, run, at=before.at)).run.status == "running"
    assert (await api.get_view(scope, run, at=after.at)).run.status == "queued"


@pytest.mark.asyncio
async def test_filtered_hidden_count_exact_attempt_and_tombstone_old_boundary():
    from app.application.execution.view_facts import attempt_key, request_key

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    activity = uuid4()
    api = service()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    await write(
        run,
        scope,
        2,
        {"activity_id": str(activity), "kind": "tool", "status": "queued"},
        kind="step",
        identity=request_key(str(activity)),
    )
    old = await api.get_view(scope, run)
    await write(
        run,
        scope,
        3,
        {
            "activity_id": str(activity),
            "kind": "tool",
            "status": "running",
            "attempt_id": "a1",
            "logical_step_id": "activity:" + str(activity),
        },
        kind="step",
        identity=attempt_key(str(activity), 0, 1),
    )
    latest = await api.get_step(scope, run, request_key(str(activity)))
    assert latest.step_id == attempt_key(str(activity), 0, 1)
    assert (
        await api.get_step(scope, run, request_key(str(activity)), at=old.at)
    ).status == "queued"
    filtered = await api.list_steps(scope, run, filters={"kind": "model"})
    assert filtered.items == []
    assert filtered.hidden_count == 1


@pytest.mark.asyncio
async def test_failed_shadow_transaction_preserves_active_and_marks_failed(monkeypatch):
    from sqlalchemy import text

    import app.infrastructure.execution.postgres_execution_view as module

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    repo = repository()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    active = await repo.rebuild_scope_shadow(scope)
    original = module._save_shadow

    async def fail_after_write(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected shadow transaction failure")

    monkeypatch.setattr(module, "_save_shadow", fail_after_write)
    with pytest.raises(RuntimeError, match="injected"):
        await repo.rebuild_scope_shadow(scope)
    async with repo.transaction() as session:
        assert await repo.active_generation(session, scope) == active.generation
        failed = await session.scalar(
            text(
                "SELECT generation FROM execution_view_generations WHERE scope_key=:scope AND status='failed'"
            ),
            {"scope": "user:" + scope.user_id},
        )
        assert failed
        assert (
            await session.scalar(
                text("SELECT count(*) FROM execution_view_shadow_runs WHERE generation=:g"),
                {"g": failed},
            )
            == 0
        )
    assert (await service().get_view(scope, run)).run.status == "running"


@pytest.mark.asyncio
async def test_activation_catches_uncommitted_progress_and_new_run_without_lost_writes(monkeypatch):
    import asyncio

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    new_run = uuid4()
    repo = repository()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    ready = asyncio.Event()
    resume = asyncio.Event()
    original = repo.activate_shadow
    calls = 0

    async def pause_once(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            ready.set()
            await resume.wait()
        return await original(*args)

    monkeypatch.setattr(repo, "activate_shadow", pause_once)
    rebuild = asyncio.create_task(repo.rebuild_scope_shadow(scope))
    await asyncio.wait_for(ready.wait(), 5)
    async with execution_admin_session() as writer:
        for rid, kind, patch in (
            (run, "step", {"progress": 75}),
            (new_run, "run", {"family": "agent", "status": "queued"}),
        ):
            await observe(
                writer,
                run_id=rid,
                owner_user_id=scope.user_id,
                team_id=None,
                family="agent",
                fact=ProjectionFact(
                    3,
                    0,
                    None,
                    kind,
                    "attempt" if kind == "step" else str(rid),
                    patch,
                    "progress" if kind == "step" else "formal",
                ),
                source_identity=str(uuid4()),
                event_id=None,
                occurred_at=datetime.now(UTC),
            )
        resume.set()
        await asyncio.sleep(0.1)
        assert not rebuild.done()
        # Existing active view remains readable while writer and switch wait.
        assert (await service().get_view(scope, run)).run.status == "running"
        await writer.commit()
    result = await asyncio.wait_for(rebuild, 10)
    assert result.activated
    assert result.captured_head.formal_position == 1
    assert result.caught_up_head.formal_position == 3
    assert calls >= 2
    assert (await service().get_step(scope, run, "attempt")).progress == 75
    assert (await service().get_view(scope, new_run)).run.status == "queued"
    await write(
        run, scope, 4, {"progress": 99}, kind="step", identity="attempt", source_kind="progress"
    )
    assert (await service().get_step(scope, run, "attempt")).progress == 99


@pytest.mark.asyncio
async def test_view_only_cli_leaves_healthy_scope_and_source_journal(monkeypatch, capsys):
    import os

    from sqlalchemy import text

    from app.rebuild_execution_projection import rebuild
    from tests.app.execution_test_support import execution_kernel_database_uri

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    monkeypatch.setenv("POSTGRES_USER", os.environ["POSTGRES_KERNEL_USER"])
    monkeypatch.setenv("POSTGRES_PASSWORD", os.environ["POSTGRES_KERNEL_PASSWORD"])
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", execution_kernel_database_uri())
    assert await rebuild("user:" + scope.user_id, view_only=True) == 0
    assert "activated" in capsys.readouterr().out
    async with execution_admin_session() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM execution_view_observations WHERE run_id=:run"),
                {"run": run},
            )
            == 1
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM execution_poisoned_scopes WHERE owner_scope_key=:scope"),
                {"scope": "user:" + scope.user_id},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_cohort_expiry_and_capture_rollback_are_explicit(monkeypatch):
    from sqlalchemy import text

    import app.infrastructure.execution.postgres_execution_view as module
    from app.application.ports.execution_view import ViewRebuilding, ViewRevisionExpired

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    api = service()
    for _ in range(2):
        await write(uuid4(), scope, 1, {"family": "agent", "status": "running"})
    monkeypatch.setattr(module, "MAX_COHORT_RUNS", 1)
    with pytest.raises(ViewRebuilding):
        await api.list_runs(scope, limit=1)
    async with execution_admin_session() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM execution_view_cohorts WHERE scope_key=:s"),
                {"s": "user:" + scope.user_id},
            )
            == 0
        )
    monkeypatch.setattr(module, "MAX_COHORT_RUNS", 100000)
    first = await api.list_runs(scope, limit=1)
    async with execution_admin_session() as session:
        await session.execute(
            text(
                "UPDATE execution_view_cohorts SET created_at=CURRENT_TIMESTAMP-interval '20 minutes',expires_at=CURRENT_TIMESTAMP-interval '5 minutes' WHERE scope_key=:s"
            ),
            {"s": "user:" + scope.user_id},
        )
        await session.commit()
    with pytest.raises(ViewRevisionExpired, match="expired"):
        await api.list_runs(scope, cursor=first.next_cursor, limit=1)
    assert await repository().cleanup_expired() > 0


@pytest.mark.asyncio
async def test_api_role_scoped_cohort_insert_and_shadow_read_only():
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.domain.models.scope import Principal
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    other = OwnerScope.personal("f04-" + str(uuid4()))
    for current in (scope, other):
        await write(uuid4(), current, 1, {"family": "agent", "status": "running"})
        await repository().rebuild_scope_shadow(current)
        await service().list_runs(current)
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    factory = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    authorization = AuthorizationContext.for_principal(Principal(user_id=scope.user_id))
    try:
        api = ExecutionViewService(
            PostgresExecutionView(session_factory=factory, authorization=authorization),
            cursor_secret=b"1234567890abcdef",
        )
        assert len((await api.list_runs(scope)).items) == 1
        async with factory() as session:
            await configure_session_authorization(session, authorization)
            for table in (
                "execution_view_generations",
                "execution_view_controls",
                "execution_view_shadow_runs",
            ):
                assert (
                    await session.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE scope_key IN (:s,:o)"),
                        {"s": "user:" + scope.user_id, "o": "user:" + other.user_id},
                    )
                    == 1
                )
        async with factory() as session:
            await configure_session_authorization(session, authorization)
            with pytest.raises(DBAPIError):
                await session.execute(
                    text("UPDATE execution_view_shadow_runs SET state='{}'::jsonb")
                )
            await session.rollback()
        async with factory() as session:
            await configure_session_authorization(session, authorization)
            with pytest.raises(DBAPIError):
                await session.execute(
                    text(
                        "INSERT INTO execution_view_cohorts(cohort_id,generation,expires_at,owner_user_id,created_by) VALUES(:id,'live',CURRENT_TIMESTAMP+interval '5 minutes',:owner,'test')"
                    ),
                    {"id": uuid4(), "owner": other.user_id},
                )
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_progress_10000_history_index_and_bounded_followup_queries():
    from sqlalchemy import text

    from app.infrastructure.models.execution_view import ExecutionViewObservationORM

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    activity = uuid4()
    now = datetime.now(UTC)
    await write(run, scope, 1, {"family": "agent", "status": "running"})
    async with execution_admin_session() as session:
        rows = [
            {
                "run_id": run,
                "observed_order": seq + 1,
                "source_kind": "progress",
                "source_identity": str(seq),
                "event_id": None,
                "formal_position": 1,
                "progress_position": seq,
                "observed_at": now,
                "occurred_at": now,
                "projection_revision": seq + 1,
                "projector_version": 1,
                "public_payload": {
                    "facts": [],
                    "applied": True,
                    "source": {
                        "activity_id": str(activity),
                        "generation": 0,
                        "claim_generation": 1,
                        "sequence": seq,
                    },
                },
                "owner_user_id": scope.user_id,
                "team_id": None,
                "created_by": "f04-scale",
            }
            for seq in range(1, 10001)
        ]
        await session.execute(ExecutionViewObservationORM.__table__.insert(), rows)
        await session.execute(
            text(
                "UPDATE execution_view_runs SET observed_order=10001,projection_revision=10001,progress_position=10000,as_of=:now,latest_available=:now WHERE run_id=:run"
            ),
            {"run": run, "now": now},
        )
        await session.execute(text("ANALYZE execution_view_observations"))
        plan = await session.scalar(
            text("""EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT public_payload['source']
            FROM execution_view_observations WHERE run_id=:run AND projector_version=1 AND source_kind='progress'
            AND public_payload['source']->>'activity_id'=:activity AND coalesce((public_payload->>'applied')::boolean,true)
            ORDER BY observed_order DESC LIMIT 1"""),
            {"run": run, "activity": str(activity)},
        )
        assert "ix_view_progress_latest_effective" in str(plan)
        assert plan[0]["Plan"]["Actual Rows"] == 1
        for seq in range(10001, 10101):
            result = await observe(
                session,
                run_id=run,
                owner_user_id=scope.user_id,
                team_id=None,
                family="agent",
                fact=ProjectionFact(0, 0, None, "step", "attempt", {"progress": 50}, "progress"),
                source_identity=str(seq),
                event_id=None,
                occurred_at=now,
                source={
                    "activity_id": str(activity),
                    "generation": 0,
                    "claim_generation": 1,
                    "sequence": seq,
                },
            )
        assert result.progress_position == 10100
        await session.rollback()


@pytest.mark.asyncio
async def test_view_exposes_first_replayable_cursor_and_missing_checkpoint_fallback():
    scope = OwnerScope.personal("f04-" + str(uuid4()))
    run = uuid4()
    api = service()
    await write(run, scope, 1, {"family": "agent", "status": "queued"})
    await write(run, scope, 2, {"status": "running"})
    latest = await api.get_view(scope, run)
    assert latest.run.first_replayable_cursor
    first = await api.get_view(scope, run, at=latest.run.first_replayable_cursor)
    assert first.run.status == "queued"
    assert first.revision == 1


@pytest.mark.asyncio
async def test_configuration_mode_time_filters_preserve_historical_configuration():
    from datetime import timedelta

    scope = OwnerScope.personal("f04-" + str(uuid4()))
    api = service()
    runs = [uuid4(), uuid4()]
    now = datetime.now(UTC)
    for run in runs:
        await write(
            run,
            scope,
            1,
            {
                "family": "agent",
                "status": "running",
                "admitted_at": now.isoformat(),
                "configuration": {"configuration_revision": "config-1"},
                "execution_mode": "recorded",
            },
        )
    filters = {
        "configuration": "config-1",
        "mode": "recorded",
        "family": "agent",
        "start": now - timedelta(seconds=1),
        "end": now + timedelta(seconds=1),
    }
    page = await api.list_runs(scope, filters, limit=1)
    remaining = next(r for r in runs if r != page.items[0].run_id)
    await write(
        remaining,
        scope,
        2,
        {"configuration": {"configuration_revision": "config-2"}, "execution_mode": "isolated"},
    )
    second = await api.list_runs(scope, filters, cursor=page.next_cursor, limit=1)
    assert second.items[0].configuration.configuration_revision == "config-1"
    assert second.items[0].execution_mode == "recorded"
    assert len((await api.list_runs(scope, filters)).items) == 1
