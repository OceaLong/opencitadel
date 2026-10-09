"""Strict owned PostgreSQL gates. Collectable without starting infrastructure."""

# ruff: noqa: F401,F811
import pytest
from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = pytest.mark.asyncio


async def authority(work, scope, principal):
    from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority
    from core.config import load_deployment_settings

    return await DBCurrentAuthority(
        work.db_session,
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    ).read_current(scope, principal)


async def test_interactive_analysis_jit_settings_are_function_local(datasets):
    from sqlalchemy.exc import DBAPIError

    service, scope, principal, *_ = datasets
    signatures = (
        "public.opencitadel_analysis_capture(text,text)",
        "public.opencitadel_analysis_manifest(text,text,jsonb)",
        "public.opencitadel_analysis_point_bindings(text,uuid,jsonb,jsonb)",
        "public.opencitadel_comparison_materialize(text,text)",
    )
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        for signature in signatures:
            assert await work.db_session.scalar(
                text(
                    "SELECT 'jit=off'=ANY(proconfig) FROM pg_proc WHERE oid=CAST(:name AS regprocedure)"
                ),
                {"name": signature},
            )
        await work.db_session.execute(text("SET LOCAL jit=on"))
        # An unauthorized call still fails and cannot leak its local optimizer
        # setting into the caller or a later query on the pooled connection.
        with pytest.raises(DBAPIError):
            async with work.db_session.begin_nested():
                await work.db_session.scalar(
                    text("SELECT public.opencitadel_analysis_capture('{}','invalid')")
                )
        assert await work.db_session.scalar(text("SELECT current_setting('jit')")) == "on"


async def test_authority_epoch_transactional_and_display_updates_do_not_bump(datasets):
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings

    service, scope, principal, *_ = datasets
    context = AuthorizationContext.for_principal(principal, scope=scope)
    secret = load_deployment_settings().database_authorization_signing_secret

    async def authorize(work, selected):
        await configure_session_authorization(work.db_session, selected, signing_secret=secret)

    async with service.uow_factory(context) as work:
        before = await authority(work, scope, principal)
        await authorize(work, AuthorizationContext.system("a01-epoch-test"))
        updated = await work.db_session.execute(
            text("UPDATE users SET last_login_at=clock_timestamp() WHERE id=:id"),
            {"id": principal.user_id},
        )
        assert updated.rowcount == 1
        await authorize(work, context)
        assert await authority(work, scope, principal) == before
        await authorize(work, AuthorizationContext.system("a01-epoch-test"))
        updated = await work.db_session.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        assert updated.rowcount == 1
        await authorize(work, context)
        with pytest.raises(PermissionError):
            await authority(work, scope, principal)
    async with service.uow_factory(context) as work:
        assert await authority(work, scope, principal) == before


async def test_api_cannot_read_or_mutate_private_epoch(datasets):
    from sqlalchemy.exc import DBAPIError

    service, scope, principal, *_ = datasets
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert await work.db_session.scalar(
            text(
                "SELECT NOT has_table_privilege(current_user,'analysis_authority_epoch','SELECT,UPDATE,DELETE,TRUNCATE')"
            )
        )
        assert await work.db_session.scalar(
            text(
                "SELECT NOT has_function_privilege(current_user,'opencitadel_analysis_authority_bump()','EXECUTE')"
            )
        )
        assert await authority(work, scope, principal) >= 0
        with pytest.raises(DBAPIError):
            await work.db_session.scalar(text("SELECT revision FROM analysis_authority_epoch"))


async def test_forged_principal_cannot_read_authority(datasets):
    service, scope, principal, *_ = datasets
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        with pytest.raises(PermissionError):
            await authority(work, scope, principal.model_copy(update={"token_version": 123}))


async def test_exact_percentiles_zero_denominator_and_dst_folds(datasets):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.application.ports.execution_analysis import AnalysisQuery
    from tests.app.infrastructure.execution.test_postgres_execution_view import write

    service, scope, principal, *_ = datasets
    for at, status, duration in [
        (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "completed", 10),
        (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "failed", 20),
        (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), "cancelled", 100),
    ]:
        await write(
            uuid4(),
            scope,
            1,
            {
                "family": "agent",
                "status": status,
                "admitted_at": at.isoformat(),
                "terminal_at": (at + timedelta(milliseconds=duration)).isoformat(),
            },
        )
    capture = await analysis_repository(service).capture(
        scope,
        principal,
        AnalysisQuery.parse(
            {"start": "2026-11-01T00:00:00Z", "end": "2026-11-02T00:00:00Z"},
            "hour",
            "America/New_York",
        ),
        None,
    )
    rows = sorted(capture.metrics["series"], key=lambda r: r["group"]["bucket"])
    assert len(rows) == 2
    assert rows[0]["metrics"]["latency_p50"]["value"] == 15
    assert rows[0]["metrics"]["latency_p95"]["value"] == 19.5
    assert rows[0]["metrics"]["success_rate"]["value"] == 0.5
    assert rows[1]["metrics"]["success_rate"]["value"] is None
    assert rows[1]["metrics"]["latency_p50"]["value"] is None
    assert rows[0]["group"]["bucket"] != rows[1]["group"]["bucket"]


async def test_two_workers_replay_fixed_metrics_after_new_terminal_fact(datasets):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.application.ports.execution_analysis import AnalysisQuery
    from app.infrastructure.repositories.db_execution_analysis_repository import (
        DBExecutionAnalysisRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.infrastructure.execution.test_postgres_execution_view import write

    service, scope, principal, *_ = datasets
    run, now = uuid4(), datetime.now(UTC)
    await write(
        run, scope, 1, {"family": "agent", "status": "running", "admitted_at": now.isoformat()}
    )
    repo = DBExecutionAnalysisRepository(
        service.uow_factory().session_factory,
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    query = AnalysisQuery.parse(
        {
            "start": (now - timedelta(seconds=1)).isoformat(),
            "end": (now + timedelta(seconds=1)).isoformat(),
        },
        "day",
        "UTC",
    )
    capture = await repo.capture(scope, principal, query, None)
    assert capture.metrics["series"][0]["metrics"]["pending"]["value"] == 1
    await write(
        run,
        scope,
        2,
        {"status": "completed", "terminal_at": (now + timedelta(milliseconds=10)).isoformat()},
    )
    other = DBExecutionAnalysisRepository(
        service.uow_factory().session_factory,
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    replay = await other.capture(scope, principal, query, capture.watermark)
    assert replay.metrics == capture.metrics
    assert await other.current(scope, principal, replay) == capture.authority


async def test_multiple_tools_preserve_unique_run_denominator(datasets):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.application.ports.execution_analysis import AnalysisQuery
    from app.infrastructure.repositories.db_execution_analysis_repository import (
        DBExecutionAnalysisRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.infrastructure.execution.test_postgres_execution_view import write

    service, scope, principal, *_ = datasets
    run, now = uuid4(), datetime.now(UTC)
    await write(
        run,
        scope,
        1,
        {
            "family": "agent",
            "status": "completed",
            "admitted_at": now.isoformat(),
            "terminal_at": (now + timedelta(milliseconds=20)).isoformat(),
        },
    )
    for i in range(2):
        await write(
            run,
            scope,
            i + 2,
            {
                "activity_id": str(uuid4()),
                "kind": "tool",
                "tool_name": "search",
                "attempt_id": str(uuid4()),
                "status": "completed",
                "started_at": now.isoformat(),
                "ended_at": (now + timedelta(milliseconds=10)).isoformat(),
            },
            kind="step",
            identity=str(uuid4()),
        )
    repo = DBExecutionAnalysisRepository(
        service.uow_factory().session_factory,
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    query = AnalysisQuery.parse(
        {
            "tool": "search",
            "start": (now - timedelta(seconds=1)).isoformat(),
            "end": (now + timedelta(seconds=1)).isoformat(),
        },
        "day",
        "UTC",
    )
    capture = await repo.capture(scope, principal, query, None)
    assert capture.metrics["series"][0]["metrics"]["run_count"]["value"] == 1
    assert capture.metrics["series"][0]["metrics"]["latency_p50"]["value"] == 20


async def test_concurrent_capture_capacity_is_atomic_and_caller_bound(datasets):
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.application.ports.execution_analysis import AnalysisQuery
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    now = datetime.now(UTC)
    query = AnalysisQuery.parse(
        {"start": (now - timedelta(days=1)).isoformat(), "end": now.isoformat()}, "day", "UTC"
    )
    left, right = await asyncio.gather(
        *(analysis_repository(service).capture(scope, principal, query, None) for _ in range(2))
    )
    assert left.watermark == right.watermark
    for index in range(20):
        query = AnalysisQuery.parse(
            {
                "start": (now - timedelta(days=1, seconds=index + 1)).isoformat(),
                "end": now.isoformat(),
            },
            "day",
            "UTC",
        )
        await analysis_repository(service).capture(scope, principal, query, None)
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        assert (
            await db.scalar(
                text("SELECT count(*) FROM analysis_captures WHERE caller_id=:id"),
                {"id": principal.user_id},
            )
            == 20
        )
    with pytest.raises(ValueError, match="analysis_refresh_required"):
        await analysis_repository(service).current(scope, principal, left)


async def test_sealed_capture_reuses_cache_after_old_capture_start(datasets):
    from datetime import UTC, datetime, timedelta

    from app.application.ports.execution_analysis import AnalysisQuery
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    now = datetime.now(UTC)
    query = AnalysisQuery.parse(
        {"start": (now - timedelta(days=1)).isoformat(), "end": now.isoformat()},
        "day",
        "UTC",
    )
    repo = analysis_repository(service)
    capture = await repo.capture(scope, principal, query, None)
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        assert await db.scalar(
            text(
                "SELECT sealed_at IS NOT NULL FROM analysis_capture_metrics WHERE capture_id=CAST(:id AS uuid)"
            ),
            {"id": capture.watermark},
        )
        # Fixture-only clock adjustment: preserve the real sealed_at, bypassing
        # the immutable-row trigger solely inside this isolated test database.
        await db.execute(
            text("ALTER TABLE analysis_captures DISABLE TRIGGER analysis_capture_immutable")
        )
        changed = await db.execute(
            text(
                "UPDATE analysis_captures SET captured_at=clock_timestamp()-interval '31 seconds' WHERE id=CAST(:id AS uuid)"
            ),
            {"id": capture.watermark},
        )
        assert changed.rowcount == 1
        await db.execute(
            text("ALTER TABLE analysis_captures ENABLE TRIGGER analysis_capture_immutable")
        )
        await db.commit()
    assert (await repo.capture(scope, principal, query, None)).watermark == capture.watermark


async def test_old_null_sealed_at_remains_readable_but_is_not_cached(datasets):
    from datetime import UTC, datetime, timedelta

    from app.application.ports.execution_analysis import AnalysisQuery
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    now = datetime.now(UTC)
    query = AnalysisQuery.parse(
        {"start": (now - timedelta(days=1)).isoformat(), "end": now.isoformat()},
        "day",
        "UTC",
    )
    repo = analysis_repository(service)
    capture = await repo.capture(scope, principal, query, None)
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        # Emulate a row sealed before 0024. It remains a valid explicit
        # watermark, but must not become a fresh short-cache candidate.
        await db.execute(
            text("ALTER TABLE analysis_capture_metrics DISABLE TRIGGER analysis_capture_immutable")
        )
        changed = await db.execute(
            text(
                "UPDATE analysis_capture_metrics SET sealed_at=NULL WHERE capture_id=CAST(:id AS uuid)"
            ),
            {"id": capture.watermark},
        )
        assert changed.rowcount == 1
        await db.execute(
            text("ALTER TABLE analysis_capture_metrics ENABLE TRIGGER analysis_capture_immutable")
        )
        await db.commit()
    assert (
        await repo.capture(scope, principal, query, capture.watermark)
    ).watermark == capture.watermark
    assert (await repo.capture(scope, principal, query, None)).watermark != capture.watermark


def analysis_repository(service):
    from app.infrastructure.repositories.db_execution_analysis_repository import (
        DBExecutionAnalysisRepository,
    )
    from core.config import load_deployment_settings

    return DBExecutionAnalysisRepository(
        service.uow_factory().session_factory,
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )


@pytest.mark.parametrize(
    "table",
    [
        "analysis_captures",
        "analysis_capture_members",
        "analysis_capture_accounting",
        "analysis_capture_metrics",
        "analysis_required_pins",
        "analysis_dataset_pin_coverage",
    ],
)
async def test_runtime_has_no_raw_capture_read_or_write(datasets, table):
    from sqlalchemy.exc import DBAPIError

    service, scope, principal, *_ = datasets
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert await work.db_session.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        )
        assert await work.db_session.scalar(
            text(
                "SELECT NOT has_table_privilege(current_user,:table,'SELECT,INSERT,UPDATE,DELETE')"
            ),
            {"table": table},
        )
        with pytest.raises(DBAPIError):
            await work.db_session.execute(text("SELECT * FROM " + table + " LIMIT 1"))


async def captured_run(datasets, *, repository=None, source=None):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.application.ports.execution_analysis import AnalysisQuery
    from tests.app.infrastructure.execution.test_postgres_execution_view import write

    service, scope, principal, *_ = datasets
    run, now = uuid4(), datetime.now(UTC)
    patch = {
        "family": "agent",
        "status": "completed",
        "admitted_at": (now - timedelta(seconds=1)).isoformat(),
        "terminal_at": now.isoformat(),
    }
    if source:
        patch["source"] = source
    await write(run, scope, 1, patch)
    query = AnalysisQuery.parse(
        {
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now + timedelta(seconds=1)).isoformat(),
        },
        "day",
        "UTC",
    )
    repo = repository or analysis_repository(service)
    return repo, query, await repo.capture(scope, principal, query, None), run


@pytest.mark.parametrize("mutation", ["role_aba", "membership_aba", "team_cascade"])
async def test_committed_raw_authority_changes_never_reuse_old_namespace(datasets, mutation):
    from uuid import uuid4

    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    team = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,'analysis')"), {"id": team})
        await db.execute(
            text("INSERT INTO team_members(team_id,user_id) VALUES(:team,:user)"),
            {"team": team, "user": principal.user_id},
        )
        await db.commit()
    repo, query, capture, _ = await captured_run(datasets)
    async with execution_admin_session() as db:
        if mutation == "role_aba":
            await db.execute(
                text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
            )
            await db.execute(
                text("UPDATE users SET global_role='user' WHERE id=:id"), {"id": principal.user_id}
            )
        elif mutation == "membership_aba":
            await db.execute(
                text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
                {"team": team, "user": principal.user_id},
            )
            await db.execute(
                text("INSERT INTO team_members(team_id,user_id) VALUES(:team,:user)"),
                {"team": team, "user": principal.user_id},
            )
        else:
            await db.execute(text("DELETE FROM teams WHERE id=:id"), {"id": team})
        await db.commit()
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert await authority(work, scope, principal) > capture.authority.revision
    with pytest.raises(ValueError, match="analysis_refresh_required"):
        await repo.capture(scope, principal, query, capture.watermark)


@pytest.mark.parametrize(
    "mutation",
    ["file_unavailable", "pin_invalidated", "pin_deleted", "session_deleted", "file_transferred"],
)
async def test_resource_only_changes_deny_saved_body_without_identity_epoch_change(
    datasets, mutation
):
    from uuid import uuid4

    from app.domain.models.resource_pin import ResourceIdentity
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    session_id, file_id, other = str(uuid4()), str(uuid4()), str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO users(id,email,username) VALUES(:id,:email,:id)"),
            {"id": other, "email": other + "@test.invalid"},
        )
        await db.execute(
            text("INSERT INTO sessions(id,owner_user_id) VALUES(:id,:owner)"),
            {"id": session_id, "owner": principal.user_id},
        )
        await db.execute(
            text(
                "INSERT INTO files(id,key,owner_user_id,content_digest,object_identity) VALUES(:id,:id,:owner,:digest,:object)"
            ),
            {"id": file_id, "owner": principal.user_id, "digest": "a" * 64, "object": str(uuid4())},
        )
        await db.commit()
    repo, query, _, run = await captured_run(
        datasets, source={"entity_type": "session", "entity_id": session_id}
    )
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        await work.resource_pins.acquire(
            scope,
            "run",
            str(run),
            [
                ResourceIdentity(
                    resource_kind="file", resource_id=file_id, resource_version="a" * 64
                )
            ],
        )
        await work.commit()
    # This new capture sees the fixed pin; the earlier capture's manifest changed.
    capture = await repo.capture(scope, principal, query, None)
    async with execution_admin_session() as db:
        if mutation == "file_unavailable":
            await db.execute(
                text("UPDATE files SET content_available=false WHERE id=:id"), {"id": file_id}
            )
        elif mutation == "pin_invalidated":
            await db.execute(
                text(
                    "UPDATE resource_pins SET available=false,unavailable_reason='force_deleted',unavailable_at=clock_timestamp() WHERE resource_id=:id"
                ),
                {"id": file_id},
            )
        elif mutation == "pin_deleted":
            await db.execute(
                text("DELETE FROM resource_pins WHERE resource_id=:id"), {"id": file_id}
            )
        elif mutation == "session_deleted":
            await db.execute(
                text("UPDATE sessions SET deleted_at=clock_timestamp() WHERE id=:id"),
                {"id": session_id},
            )
        else:
            await db.execute(
                text("UPDATE files SET owner_user_id=:owner WHERE id=:id"),
                {"id": file_id, "owner": other},
            )
        await db.commit()
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert await authority(work, scope, principal) == capture.authority.revision
    with pytest.raises(ValueError, match="analysis_refresh_required"):
        await repo.capture(scope, principal, query, capture.watermark)
    new = await repo.capture(scope, principal, query, None)
    assert new.metrics["series"] == []


async def test_fresh_final_check_observes_revocation_hidden_from_capture_snapshot(datasets):
    import asyncio

    from app.application.services.execution_analysis_service import ExecutionAnalysisService
    from app.infrastructure.repositories.db_execution_analysis_repository import (
        DBExecutionAnalysisRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    _, query, _, _ = await captured_run(datasets)
    started, resume = asyncio.Event(), asyncio.Event()

    class Paused(DBExecutionAnalysisRepository):
        async def _operation(self, db, scope, principal, operation, **payload):
            result = await super()._operation(db, scope, principal, operation, **payload)
            if operation == "begin":
                started.set()
                await asyncio.wait_for(resume.wait(), 60)
            return result

    repository = Paused(
        service.uow_factory().session_factory,
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    summary = ExecutionAnalysisService(repository, principal)
    pending = asyncio.create_task(
        summary.summary(
            scope, {"start": query.start.isoformat(), "end": query.end.isoformat()}, "day", "UTC"
        )
    )
    await asyncio.wait_for(started.wait(), 60)
    try:
        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET status='disabled' WHERE id=:id"), {"id": principal.user_id}
            )
            await db.commit()
    finally:
        resume.set()
    with pytest.raises((PermissionError, ValueError), match="analysis_"):
        await pending


async def test_primary_cap_plus_one_rejects_without_partial_capture(datasets):
    from tests.app.execution_test_support import execution_admin_session

    _service, scope, principal, *_ = datasets
    repo, query, original, run = await captured_run(datasets)
    async with execution_admin_session() as db:
        await db.execute(
            text("""INSERT INTO execution_view_runs(run_id,family,status,purpose,admitted_at,terminal_at,completeness,capabilities,projection_revision,projector_version,formal_position,progress_position,observed_order,owner_user_id,team_id,created_by)
          SELECT gen_random_uuid(),family,status,purpose,admitted_at,terminal_at,completeness,capabilities,projection_revision,projector_version,formal_position,progress_position,observed_order,owner_user_id,team_id,created_by FROM execution_view_runs CROSS JOIN generate_series(1,100000) WHERE run_id=:run"""),
            {"run": run},
        )
        before = await db.scalar(text("SELECT count(*) FROM analysis_captures"))
        await db.commit()
    from dataclasses import replace

    # Different identity prevents the legitimate 30-second dedupe from hiding new members.
    changed = replace(query, filters=(("family", "agent"),))
    with pytest.raises(ValueError, match="analysis_capacity_exceeded"):
        await repo.capture(scope, principal, changed, None)
    async with execution_admin_session() as db:
        assert await db.scalar(text("SELECT count(*) FROM analysis_captures")) == before
        assert (
            await db.scalar(
                text("SELECT count(*) FROM execution_view_runs WHERE owner_user_id=:id"),
                {"id": principal.user_id},
            )
            == 100001
        )
    assert (
        await repo.capture(scope, principal, query, original.watermark)
    ).metrics == original.metrics


async def test_physical_settlement_publication_cut_survives_worker_replay(datasets):
    from dataclasses import replace
    from uuid import uuid4

    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.execution.test_postgres_execution_view import write

    service, scope, principal, *_ = datasets
    repo, query, _, run = await captured_run(datasets)
    call, config, activity = str(uuid4()), str(uuid4()), uuid4()
    values = {
        "owner": principal.user_id,
        "run": run,
        "call": call,
        "config": config,
        "activity": activity,
        "event": uuid4(),
        "fact": '{"usage":{"prompt_tokens":12,"completion_tokens":3},"cost_usd":"0.25"}',
    }
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO execution_configurations(id,run_id,body,purpose,owner_user_id,created_by) VALUES(:config,:run,'{}','evaluation_judge',:owner,:owner)"
            ),
            values,
        )
        await db.execute(
            text(
                "INSERT INTO execution_model_dispatches(call_identity,run_id,activity_id,generation,claim_generation,attempt_id,logical_group,ordinal,configuration_id,request_snapshot,owner_user_id,created_by) VALUES(:call,:run,:activity,0,1,:call,'physical',1,:config,'{}',:owner,:owner)"
            ),
            values,
        )
        await db.execute(
            text(
                "INSERT INTO execution_usage_publications(call_identity,phase,event_id,event_position,owner_user_id,created_by) VALUES(:call,'dispatch',:event,1,:owner,:owner)"
            ),
            values,
        )
        await db.execute(
            text(
                "INSERT INTO execution_model_settlements(call_identity,fact,owner_user_id,created_by) VALUES(:call,CAST(:fact AS jsonb),:owner,:owner)"
            ),
            values,
        )
        await db.execute(
            text(
                "INSERT INTO execution_usage_publications(call_identity,phase,event_id,event_position,owner_user_id,created_by) VALUES(:call,'settlement',:event,2,:owner,:owner)"
            ),
            {**values, "event": uuid4()},
        )
        await db.commit()
    fixed = replace(query, filters=(("family", "agent"),))
    before = await repo.capture(scope, principal, fixed, None)
    usage = before.metrics["usage"]["purposes"]["evaluation_judge"]
    assert usage["input_tokens"]["value"] is None
    assert usage["cost_usd"]["missing_count"] == 1
    await write(run, scope, 2, {"status": "completed"})
    replay = await analysis_repository(service).capture(scope, principal, fixed, before.watermark)
    assert replay.metrics == before.metrics
    after = await repo.capture(
        scope, principal, replace(query, filters=(("status", "completed"),)), None
    )
    actual = after.metrics["usage"]["purposes"]["evaluation_judge"]
    assert actual["input_tokens"]["value"] == 12
    assert actual["cost_usd"]["value"] == "0.25"
    assert actual["cost_usd"]["sample_count"] == 1


async def published_analysis_dataset(datasets):
    from uuid import uuid4

    from app.domain.evaluation.dataset import CaseRevision
    from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import draft

    service, scope, principal, *_ = datasets
    initial = await draft(service, scope, principal)
    current = await service.update_case(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        case=CaseRevision(case_key="one", input="fixed input"),
    )
    return await service.publish(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=current.revision,
    )


async def test_legacy_version_certification_uses_fixed_objects_without_creating_pins(datasets):
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    version = await published_analysis_dataset(datasets)
    async with execution_admin_session() as db:
        # Simulate an existing immutable version with no pre-0016 certification.
        await db.execute(
            text("DELETE FROM analysis_dataset_pin_coverage WHERE version_id=:id"),
            {"id": version.id},
        )
        before = await db.scalar(text("SELECT count(*) FROM resource_pins"))
        await db.commit()
    assert await service.certify_analysis_version(scope, principal, version.id) == {
        "status": "certified"
    }
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM analysis_dataset_pin_coverage WHERE version_id=:id"),
                {"id": version.id},
            )
            == 1
        )
        assert await db.scalar(text("SELECT count(*) FROM resource_pins")) == before


async def test_legacy_corrupt_object_cannot_certify_or_recreate_missing_pin(datasets):
    from app.domain.evaluation.errors import DatasetUnavailable
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, objects, *_ = datasets
    version = await published_analysis_dataset(datasets)
    async with execution_admin_session() as db:
        await db.execute(
            text("DELETE FROM analysis_dataset_pin_coverage WHERE version_id=:id"),
            {"id": version.id},
        )
        key = await db.scalar(
            text(
                "SELECT o.storage_key FROM evaluation_version_cases v JOIN evaluation_case_revisions c ON c.scope_key=v.scope_key AND c.id=v.case_revision_id JOIN evaluation_object_intents o ON o.scope_key=c.scope_key AND o.id=c.object_id WHERE v.version_id=:id"
            ),
            {"id": version.id},
        )
        await db.commit()
    await objects.put_bytes(key, b"[]")
    with pytest.raises(DatasetUnavailable, match="case_body_changed"):
        await service.certify_analysis_version(scope, principal, version.id)
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM analysis_dataset_pin_coverage WHERE version_id=:id"),
                {"id": version.id},
            )
            == 0
        )


async def test_publication_failure_rolls_back_certification_and_version(datasets, monkeypatch):
    from uuid import uuid4

    from app.domain.evaluation.dataset import CaseRevision
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import draft

    service, scope, principal, *_ = datasets
    initial = await draft(service, scope, principal)
    current = await service.update_case(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        case=CaseRevision(case_key="one", input="fixed"),
    )

    async def fail_after_certification(*args, **kwargs):
        raise RuntimeError("injected_finish_failure")

    monkeypatch.setattr(service, "_finish", fail_after_certification)
    with pytest.raises(RuntimeError, match="injected_finish_failure"):
        await service.publish(
            scope,
            principal,
            dataset_id=initial.id,
            request_id=str(uuid4()),
            expected_revision=current.revision,
        )
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM evaluation_dataset_versions WHERE dataset_id=:id"),
                {"id": initial.id},
            )
            == 0
        )
        assert await db.scalar(text("SELECT count(*) FROM analysis_dataset_pin_coverage")) == 0


async def test_signed_capture_tampering_and_cross_caller_watermark_fail_closed(datasets):
    from sqlalchemy.exc import DBAPIError

    from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority

    _service, scope, principal, *_ = datasets
    repo, query, capture, _ = await captured_run(datasets)
    async with repo.transaction(scope, principal) as db:
        signed = await DBCurrentAuthority(db, signing_secret=repo.secret).signed(
            scope, principal, operation="read", capture_id=capture.watermark
        )
        with pytest.raises(DBAPIError, match="analysis_authorization"):
            await db.scalar(
                text("SELECT public.opencitadel_analysis_capture(:body,:signature)"),
                {**signed, "body": signed["body"].replace('"read"', '"seal"')},
            )
    from uuid import uuid4

    from app.domain.models.scope import OwnerScope, Principal
    from tests.app.execution_test_support import execution_admin_session

    other = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO users(id,email,username) VALUES(:id,:email,:id)"),
            {"id": other, "email": other + "@test.invalid"},
        )
        await db.commit()
    with pytest.raises(ValueError, match="analysis_refresh_required"):
        await repo.capture(
            OwnerScope.personal(other), Principal(user_id=other), query, capture.watermark
        )


async def test_expired_capture_refuses_replay_and_kernel_cleanup_is_bounded(
    datasets, isolated_database
):
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    _service, scope, principal, *_ = datasets
    repo, query, capture, _ = await captured_run(datasets)
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        # Fixture-only expiry manipulation; runtime UPDATE is denied and immutable.
        await db.execute(
            text("ALTER TABLE analysis_captures DISABLE TRIGGER analysis_capture_immutable")
        )
        changed = await db.execute(
            text(
                "UPDATE analysis_captures SET expires_at=clock_timestamp()-interval '1 second' WHERE id=CAST(:id AS uuid)"
            ),
            {"id": capture.watermark},
        )
        assert changed.rowcount == 1
        await db.execute(
            text("ALTER TABLE analysis_captures ENABLE TRIGGER analysis_capture_immutable")
        )
        await db.commit()
    with pytest.raises(ValueError, match="analysis_refresh_required"):
        await repo.capture(scope, principal, query, capture.watermark)
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.infrastructure.security.db_authorization import configure_session_authorization
    from tests.app.execution_test_support import (
        authenticated_session_factory,
        execution_kernel_database_uri,
    )

    engine, _ = isolated_database
    kernel = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=engine.url.database),
        pool_size=1,
        max_overflow=0,
    )
    try:
        async with authenticated_session_factory(kernel, signing_secret=repo.secret)() as db:
            await configure_session_authorization(
                db, AuthorizationContext.system("execution-kernel"), signing_secret=repo.secret
            )
            assert await repo.cleanup_expired(db, limit=1) == 1
            await db.commit()
        live = await repo.capture(scope, principal, query, None)
        assert live.watermark != capture.watermark
        async with authenticated_session_factory(kernel, signing_secret=repo.secret)() as db:
            await configure_session_authorization(
                db, AuthorizationContext.system("execution-kernel"), signing_secret=repo.secret
            )
            assert await repo.cleanup_expired(db, limit=1) == 0
            await db.commit()
        assert (
            await repo.capture(scope, principal, query, live.watermark)
        ).watermark == live.watermark
    finally:
        await kernel.dispose()


async def test_partial_human_heads_keep_model_source_and_exact_evaluation_revision(
    budget_binding_fixture, datasets
):
    from app.application.ports.execution_analysis import AnalysisQuery
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import (
        test_partial_review_after_completed_preserves_model_and_history,
    )

    await test_partial_review_after_completed_preserves_model_and_history(budget_binding_fixture)
    service, scope, principal, *_ = datasets
    capture = await analysis_repository(service).capture(
        scope, principal, AnalysisQuery.parse({}, "day", "UTC"), None
    )
    scores = capture.metrics["scores"]
    assert len(scores["series"]) == 1
    metrics = scores["series"][0]["metrics"]
    assert metrics["model:correctness:mean"]["value"] == 4
    assert metrics["human:correctness:mean"]["value"] == 2
    assert metrics["human:completeness:mean"]["value"] == 1
    assert metrics["confirmed_pass_rate"]["value"] == 0
    assert scores["evaluation_cuts"][0]["evaluation_revision"] == 4
    assert "private review reason" not in str(capture.metrics)


async def test_selected_result_accounts_outside_cohort_retry_and_batch_total_is_distinct(
    budget_binding_fixture, datasets
):
    from dataclasses import replace
    from datetime import timedelta
    from uuid import uuid4

    from app.application.ports.execution_analysis import AnalysisQuery
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.execution.test_postgres_execution_view import write
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup

    review, scope, principal, batch, candidate, payload = await review_setup(budget_binding_fixture)
    await review.append_score(scope, principal, candidate.result_id, 0, str(uuid4()), payload)
    retry, other, other_result = uuid4(), uuid4(), uuid4()
    async with execution_admin_session() as db:
        admitted = await db.scalar(
            text("SELECT admitted_at FROM execution_view_runs WHERE run_id=:run"),
            {"run": candidate.run_id},
        )
        await configure_session_authorization(
            db,
            AuthorizationContext.system("execution-kernel"),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        await db.execute(
            text(
                "INSERT INTO evaluation_batch_attempts(result_id,attempt,run_id,command_id,admission_key,owner_user_id,created_by) VALUES(:result,1,:run,:command,:key,:owner,:owner)"
            ),
            {
                "result": candidate.result_id,
                "run": retry,
                "command": uuid4(),
                "key": str(uuid4()),
                "owner": principal.user_id,
            },
        )
        await db.execute(
            text(
                "INSERT INTO evaluation_batch_results(id,batch_id,case_revision_id,config_version_id,repetition,ordinal,owner_user_id,created_by) SELECT :id,batch_id,case_revision_id,config_version_id,1,1,owner_user_id,created_by FROM evaluation_batch_results WHERE id=:result"
            ),
            {"id": other_result, "result": candidate.result_id},
        )
        await db.execute(
            text(
                "INSERT INTO evaluation_batch_attempts(result_id,attempt,run_id,command_id,admission_key,owner_user_id,created_by) VALUES(:result,0,:run,:command,:key,:owner,:owner)"
            ),
            {
                "result": other_result,
                "run": other,
                "command": uuid4(),
                "key": str(uuid4()),
                "owner": principal.user_id,
            },
        )
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM evaluation_batch_attempts WHERE run_id IN (:retry,:other)"
                ),
                {"retry": retry, "other": other},
            )
            == 2
        )
        await db.commit()
    for run in (retry, other):
        await write(
            run,
            scope,
            1,
            {
                "family": "agent",
                "status": "completed",
                "admitted_at": (admitted - timedelta(days=2)).isoformat(),
                "terminal_at": (admitted - timedelta(days=2) + timedelta(seconds=1)).isoformat(),
            },
        )
    async with execution_admin_session() as db:
        for run, cost in [(candidate.run_id, 1), (retry, 2), (other, 4)]:
            call, config = str(uuid4()), str(uuid4())
            values = {
                "owner": principal.user_id,
                "run": run,
                "call": call,
                "config": config,
                "activity": uuid4(),
                "event": uuid4(),
                "fact": '{"cost_usd":"' + str(cost) + '"}',
            }
            await db.execute(
                text(
                    "INSERT INTO execution_configurations(id,run_id,body,purpose,owner_user_id,created_by) VALUES(:config,:run,'{}','evaluation_subject',:owner,:owner)"
                ),
                values,
            )
            await db.execute(
                text(
                    "INSERT INTO execution_model_dispatches(call_identity,run_id,activity_id,generation,claim_generation,attempt_id,logical_group,ordinal,configuration_id,request_snapshot,owner_user_id,created_by) VALUES(:call,:run,:activity,0,1,:call,'physical',1,:config,'{}',:owner,:owner)"
                ),
                values,
            )
            await db.execute(
                text(
                    "INSERT INTO execution_model_settlements(call_identity,fact,owner_user_id,created_by) VALUES(:call,CAST(:fact AS jsonb),:owner,:owner)"
                ),
                values,
            )
            for phase in ("dispatch", "settlement"):
                await db.execute(
                    text(
                        "INSERT INTO execution_usage_publications(call_identity,phase,event_id,event_position,owner_user_id,created_by) VALUES(:call,:phase,:event,1,:owner,:owner)"
                    ),
                    {**values, "phase": phase, "event": uuid4()},
                )
        await db.commit()
    repo = analysis_repository(datasets[0])
    query = AnalysisQuery.parse(
        {
            "start": (admitted - timedelta(seconds=1)).isoformat(),
            "end": (admitted + timedelta(seconds=1)).isoformat(),
            "accounting": "selected_result",
        },
        "day",
        "UTC",
    )
    selected = await repo.capture(scope, principal, query, None)
    whole = await repo.capture(
        scope,
        principal,
        replace(query, filters=(("accounting", "batch_total"), ("batch_id", str(batch.id)))),
        None,
    )
    assert sum(row["metrics"]["run_count"]["value"] for row in selected.metrics["series"]) == 1
    assert sum(row["metrics"]["run_count"]["value"] for row in whole.metrics["series"]) == 1
    assert selected.metrics["usage"]["grain"] == "selected_result"
    assert selected.metrics["usage"]["accounting_run_count"] == 2
    assert selected.metrics["usage"]["purposes"]["evaluation_subject"]["cost_usd"]["value"] == "3"
    assert whole.metrics["usage"]["grain"] == "batch_total"
    assert whole.metrics["usage"]["accounting_run_count"] == 3
    assert whole.metrics["usage"]["purposes"]["evaluation_subject"]["cost_usd"]["value"] == "7"


async def test_accounting_cap_plus_one_rolls_back_every_capture_row(
    budget_binding_fixture, datasets
):
    from app.application.ports.execution_analysis import AnalysisQuery
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_judge_repository import (
        test_durable_judge_intent_is_current_and_idempotent,
    )

    await test_durable_judge_intent_is_current_and_idempotent(budget_binding_fixture)
    service, scope, principal, *_ = datasets
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.system("execution-kernel"),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        assert (
            await db.scalar(
                text("SELECT count(*) FROM evaluation_judge_intents WHERE rescore IS NULL")
            )
            == 1
        )
        # One original subject plus one initial judge plus 999999 distinct rescore Runs.
        await db.execute(
            text("""INSERT INTO evaluation_judge_intents(id,run_id,batch_id,result_id,namespace_id,rubric_id,config_id,protocol,candidate,materials,request_id,fingerprint,rescore,authorizer,owner_user_id,team_id,created_by)
          SELECT gen_random_uuid(),gen_random_uuid(),j.batch_id,j.result_id,j.namespace_id,j.rubric_id,j.config_id,1,j.candidate,'{}', 'analysis-cap-'||g::text,j.fingerprint,'{}',b.principal,j.owner_user_id,j.team_id,j.created_by
          FROM evaluation_judge_intents j JOIN evaluation_batches b ON b.scope_key=j.scope_key AND b.id=j.batch_id CROSS JOIN generate_series(1,999999) g WHERE j.rescore IS NULL""")
        )
        assert (
            await db.scalar(text("SELECT count(DISTINCT run_id) FROM evaluation_judge_intents"))
            == 1000000
        )
        before = await db.scalar(text("SELECT count(*) FROM analysis_captures"))
        await db.commit()
    with pytest.raises(ValueError, match="analysis_accounting_capacity_exceeded"):
        await analysis_repository(service).capture(
            scope,
            principal,
            AnalysisQuery.parse({"accounting": "selected_result"}, "day", "UTC"),
            None,
        )
    async with execution_admin_session() as db:
        assert await db.scalar(text("SELECT count(*) FROM analysis_captures")) == before
        assert await db.scalar(text("SELECT count(*) FROM analysis_capture_accounting")) == 0


async def test_exact_invalidated_model_set_does_not_erase_rescore_rubric(
    budget_binding_fixture, datasets
):
    from app.application.ports.execution_analysis import AnalysisQuery
    from tests.app.infrastructure.repositories.test_evaluation_judge_repository import (
        test_rescore_new_rubric_preserves_history_and_terminal_execution,
    )

    await test_rescore_new_rubric_preserves_history_and_terminal_execution(budget_binding_fixture)
    service, scope, principal, *_ = datasets
    original_rubric = str(budget_binding_fixture[3].rubric_version)
    result = await analysis_repository(service).capture(
        scope, principal, AnalysisQuery.parse({}, "day", "UTC"), None
    )
    strata = result.metrics["scores"]["series"]
    original = next(row for row in strata if row["identity"][4] == original_rubric)
    replacement = next(row for row in strata if row["identity"][4] != original_rubric)
    assert original["metrics"]["model:correctness:mean"]["value"] is None
    assert original["metrics"]["model:correctness:mean"]["excluded_count"] == 1
    assert replacement["metrics"]["model:correctness:mean"]["value"] == 4
    assert replacement["metrics"]["model:correctness:mean"]["excluded_count"] == 0
    assert result.metrics["scores"]["evaluation_cuts"][0]["evaluation_revision"] == 4


@pytest.mark.parametrize("mutation", ["missing_pin", "deleted_resource"])
async def test_legacy_certification_requires_each_current_owned_resource(datasets, mutation):
    from uuid import uuid4

    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import draft

    service, scope, principal, *_ = datasets
    file_id = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO files(id,key,owner_user_id,content_digest,object_identity) VALUES(:id,:id,:owner,:digest,:object)"
            ),
            {"id": file_id, "owner": principal.user_id, "digest": "b" * 64, "object": str(uuid4())},
        )
        await db.commit()
    initial = await draft(service, scope, principal)
    current = await service.update_case(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        case=CaseRevision(
            case_key="one",
            input="fixed",
            resources=(
                ResourceIdentity(
                    resource_kind="file", resource_id=file_id, resource_version="b" * 64
                ),
            ),
        ),
    )
    version = await service.publish(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=current.revision,
    )
    async with execution_admin_session() as db:
        await db.execute(
            text("DELETE FROM analysis_dataset_pin_coverage WHERE version_id=:id"),
            {"id": version.id},
        )
        if mutation == "missing_pin":
            await db.execute(
                text(
                    "DELETE FROM resource_pins WHERE owner_kind='dataset_version' AND owner_id=:id"
                ),
                {"id": str(version.id)},
            )
        else:
            await db.execute(
                text("UPDATE files SET content_available=false WHERE id=:id"), {"id": file_id}
            )
        before = await db.scalar(text("SELECT count(*) FROM resource_pins"))
        await db.commit()
    with pytest.raises(ResourceUnavailable, match="unavailable"):
        await service.certify_analysis_version(scope, principal, version.id)
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM analysis_dataset_pin_coverage WHERE version_id=:id"),
                {"id": version.id},
            )
            == 0
        )
        assert await db.scalar(text("SELECT count(*) FROM resource_pins")) == before
