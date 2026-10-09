"""Signed tenant authorization and least-authority proofs for execution views."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole
from app.infrastructure.models.execution_view import metadata
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
    configure_sync_system_authorization,
)
from app.infrastructure.security.tenant_rls import EXECUTION_VIEW_TABLES
from core.config import load_deployment_settings, sqlalchemy_sync_migration_database_uri
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
)

pytestmark = pytest.mark.usefixtures("postgres_integration")
NOW = datetime(2026, 9, 7, tzinfo=UTC)


def run_values(run_id, owner=None, team=None):
    return {
        "run_id": run_id,
        "owner_user_id": owner,
        "team_id": team,
        "created_by": "f01-test",
        "family": "agent",
        "status": "running",
        "purpose": "production",
        "completeness": {},
        "capabilities": [],
        "projection_revision": 1,
        "projector_version": 1,
        "formal_position": 0,
        "progress_position": 0,
        "observed_order": 0,
    }


@pytest.fixture
def seeded_views():
    settings = load_deployment_settings()
    engine = sa.create_engine(sqlalchemy_sync_migration_database_uri(settings))
    tag = "f01-" + uuid4().hex
    run_ids = [uuid4(), uuid4()]
    try:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="f01-seed",
                signing_secret=settings.database_authorization_signing_secret,
            )
            connection.execute(
                sa.text("INSERT INTO users (id,email,username) VALUES (:tag,:email,:tag)"),
                {"tag": tag, "email": tag + "@example.test"},
            )
            connection.execute(
                sa.text("INSERT INTO teams (id,name) VALUES (:tag,:tag)"), {"tag": tag}
            )
            for index, run_id in enumerate(run_ids):
                owner, team = (tag, None) if index == 0 else (None, tag)
                connection.execute(
                    sa.text(
                        "INSERT INTO sessions (id,owner_user_id,team_id) VALUES (:id,:owner,:team)"
                    ),
                    {"id": str(run_id), "owner": tag, "team": team},
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO artifacts (id,session_id,version_refs) VALUES (:id,:id,'[\"fixture-content\"]'::jsonb)"
                    ),
                    {"id": str(run_id)},
                )
                common = {"owner_user_id": owner, "team_id": team, "created_by": tag}
                connection.execute(
                    metadata.tables["execution_view_runs"].insert(), run_values(run_id, owner, team)
                )
                rows = {
                    "execution_view_steps": {
                        "id": uuid4(),
                        "run_id": run_id,
                        "step_id": "s",
                        "relationship": "unknown",
                        "kind": "tool",
                        "status": "running",
                        "projection_revision": 1,
                        "observed_order": 0,
                        "completeness": {},
                    },
                    "execution_view_checkpoints": {
                        "id": uuid4(),
                        "run_id": run_id,
                        "boundary": 0,
                        "projector_version": 1,
                        "formal_position": 0,
                        "progress_position": 0,
                        "observed_order": 0,
                        "projection_revision": 1,
                        "observed_at": NOW,
                        "state_ref": {},
                    },
                    "execution_view_observations": {
                        "run_id": run_id,
                        "observed_order": 1,
                        "source_kind": "formal",
                        "source_identity": "source",
                        "formal_position": 1,
                        "progress_position": 0,
                        "observed_at": NOW,
                        "projection_revision": 1,
                        "projector_version": 1,
                        "public_payload": {},
                    },
                    "artifact_version_provenance": {
                        "id": uuid4(),
                        "artifact_id": str(run_id),
                        "version": 1,
                        "producer_identity": "pending",
                        "producer_run_id": None,
                        "evidence_kind": "unknown",
                        "binding_status": "pending",
                        "availability": "unknown",
                        "revision": 1,
                    },
                    "execution_usage_facts": {
                        "id": uuid4(),
                        "run_id": run_id,
                        "call_identity": "call",
                        "purpose": "production",
                        "coverage": {},
                        "revision": 1,
                    },
                }
                for name, row in rows.items():
                    connection.execute(metadata.tables[name].insert(), row | common)
        yield tag, run_ids
    finally:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="f01-cleanup",
                signing_secret=settings.database_authorization_signing_secret,
            )
            for name in [
                *sorted(EXECUTION_VIEW_TABLES - {"execution_view_runs"}),
                "execution_view_runs",
            ]:
                table = metadata.tables[name]
                connection.execute(
                    table.delete().where(
                        sa.or_(table.c.owner_user_id == tag, table.c.team_id == tag)
                    )
                )
            connection.execute(
                sa.text("DELETE FROM sessions WHERE id IN (:a,:b)"),
                {"a": str(run_ids[0]), "b": str(run_ids[1])},
            )
            connection.execute(sa.text("DELETE FROM teams WHERE id=:tag"), {"tag": tag})
            connection.execute(sa.text("DELETE FROM users WHERE id=:tag"), {"tag": tag})
        engine.dispose()


def factory(engine):
    return authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().database_authorization_signing_secret
    )


def test_personal_team_auditor_and_unsigned_visibility(seeded_views):
    tag, _ = seeded_views

    async def scenario():
        engine = create_async_engine(load_deployment_settings().sqlalchemy_database_uri)
        contexts = [
            (AuthorizationContext.anonymous(), 0),
            (AuthorizationContext.for_principal(Principal(user_id=tag)), 1),
            (
                AuthorizationContext.for_principal(
                    Principal(user_id=tag, team_roles={tag: TeamRole.MEMBER}),
                    scope=OwnerScope.team(tag, tag),
                ),
                1,
            ),
            (AuthorizationContext.for_principal(Principal(user_id="stranger")), 0),
            (
                AuthorizationContext.for_principal(
                    Principal(user_id="auditor", global_role=GlobalRole.AUDITOR)
                ),
                2,
            ),
        ]
        try:
            for context, count in contexts:
                async with factory(engine)() as session:
                    await configure_session_authorization(session, context)
                    for name in sorted(EXECUTION_VIEW_TABLES):
                        table = metadata.tables[name]
                        actual = await session.scalar(
                            sa.select(sa.func.count())
                            .select_from(table)
                            .where(sa.or_(table.c.owner_user_id == tag, table.c.team_id == tag))
                        )
                        assert actual == count, (name, context)
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session, AuthorizationContext.for_principal(Principal(user_id="stranger"))
                )
                await session.execute(
                    sa.text("SELECT set_config('app.user_id', :owner, true)"), {"owner": tag}
                )
                assert (
                    await session.scalar(sa.text("SELECT count(*) FROM execution_view_runs")) == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_runtime_privileges_and_kernel_writes(seeded_views):
    tag, run_ids = seeded_views

    async def scenario():
        settings = load_deployment_settings()
        for kernel in [False, True]:
            engine = create_async_engine(
                execution_kernel_database_uri() if kernel else settings.sqlalchemy_database_uri
            )
            try:
                async with factory(engine)() as session:
                    await configure_session_authorization(
                        session, AuthorizationContext.system("f01-privileges")
                    )
                    assert (
                        await session.execute(
                            sa.text(
                                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
                            )
                        )
                    ).one() == (False, False)
                    for name in sorted(EXECUTION_VIEW_TABLES):
                        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                            allowed = (
                                kernel
                                or privilege == "SELECT"
                                or (
                                    name == "artifact_version_provenance"
                                    and privilege in ("INSERT", "UPDATE")
                                )
                            )
                            if name == "artifact_version_provenance" and privilege == "DELETE":
                                allowed = False
                            actual = await session.scalar(
                                sa.text(
                                    "SELECT has_table_privilege(current_user,:table,:privilege)"
                                ),
                                {"table": name, "privilege": privilege},
                            )
                            assert actual is allowed, (kernel, name, privilege)
                        assert (
                            await session.execute(
                                sa.text(
                                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=CAST(:table AS regclass)"
                                ),
                                {"table": name},
                            )
                        ).one() == (True, True)
                    assert not await session.scalar(
                        sa.text(
                            "SELECT has_table_privilege(current_user,'execution_events','UPDATE')"
                        )
                    )
                    assert not await session.scalar(
                        sa.text(
                            "SELECT has_table_privilege(current_user,'execution_events','DELETE')"
                        )
                    )
                    if kernel:
                        result = await session.execute(
                            sa.text(
                                "UPDATE execution_view_runs SET public_summary='written' WHERE run_id=:run"
                            ),
                            {"run": run_ids[0]},
                        )
                        assert result.rowcount == 1
                        table = metadata.tables["execution_view_runs"]
                        new_id = uuid4()
                        await session.execute(table.insert(), run_values(new_id, tag))
                        assert (
                            await session.execute(table.delete().where(table.c.run_id == new_id))
                        ).rowcount == 1
                    else:
                        with pytest.raises(DBAPIError, match="permission denied"):
                            await session.execute(
                                sa.text(
                                    "UPDATE execution_view_runs SET status=status WHERE run_id=:run"
                                ),
                                {"run": run_ids[0]},
                            )
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_kernel_auditor_cannot_write_and_scope_cannot_be_forged(seeded_views):
    tag, run_ids = seeded_views

    async def scenario():
        engine = create_async_engine(execution_kernel_database_uri())
        try:
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session,
                    AuthorizationContext.for_principal(
                        Principal(user_id="auditor", global_role=GlobalRole.AUDITOR)
                    ),
                )
                for name in EXECUTION_VIEW_TABLES:
                    table = metadata.tables[name]
                    result = await session.execute(
                        table.update()
                        .where(table.c.owner_user_id == tag)
                        .values(created_by="tampered")
                    )
                    assert result.rowcount == 0
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session, AuthorizationContext.system("f01-scope-proof")
                )
                with pytest.raises(DBAPIError, match="foreign key"):
                    await session.execute(
                        metadata.tables["execution_view_steps"].insert(),
                        {
                            "id": uuid4(),
                            "run_id": run_ids[0],
                            "step_id": "wrong-scope",
                            "owner_user_id": "stranger",
                            "created_by": tag,
                            "relationship": "unknown",
                            "kind": "tool",
                            "status": "unknown",
                            "projection_revision": 1,
                            "observed_order": 0,
                            "completeness": {},
                        },
                    )
            for owner, team in [(None, None), (tag, tag)]:
                async with factory(engine)() as session:
                    await configure_session_authorization(
                        session, AuthorizationContext.system("f01-owner-proof")
                    )
                    with pytest.raises(DBAPIError, match=r"check constraint|not-null constraint"):
                        await session.execute(
                            metadata.tables["execution_view_runs"].insert(),
                            run_values(uuid4(), owner, team),
                        )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_provenance_rejects_cross_scope_artifact_and_retains_deleted_reference(seeded_views):
    tag, run_ids = seeded_views

    async def scenario():
        engine = create_async_engine(load_deployment_settings().sqlalchemy_database_uri)
        table = metadata.tables["artifact_version_provenance"]
        try:
            # Even a system writer cannot attach a personal row to a team artifact.
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session, AuthorizationContext.system("f01-provenance-scope")
                )
                with pytest.raises(DBAPIError, match="artifact scope mismatch"):
                    await session.execute(
                        table.insert(),
                        {
                            "id": uuid4(),
                            "artifact_id": str(run_ids[1]),
                            "version": 1,
                            "producer_identity": "wrong",
                            "owner_user_id": tag,
                            "created_by": tag,
                            "evidence_kind": "unknown",
                            "binding_status": "pending",
                            "availability": "unknown",
                            "revision": 1,
                        },
                    )
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session, AuthorizationContext.for_principal(Principal(user_id=tag))
                )
                await session.execute(
                    table.insert(),
                    {
                        "id": uuid4(),
                        "artifact_id": str(run_ids[0]),
                        "version": 1,
                        "producer_identity": "second",
                        "owner_user_id": tag,
                        "created_by": tag,
                        "evidence_kind": "direct",
                        "binding_status": "pending",
                        "availability": "available",
                        "revision": 1,
                    },
                )
                with pytest.raises(DBAPIError, match="artifact scope mismatch"):
                    await session.execute(
                        table.update()
                        .where(table.c.owner_user_id == tag)
                        .values(artifact_id=str(run_ids[1]))
                    )
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session, AuthorizationContext.for_principal(Principal(user_id=tag))
                )
                await session.execute(
                    sa.text("DELETE FROM artifacts WHERE id=:id"), {"id": str(run_ids[0])}
                )
                result = await session.execute(
                    table.update()
                    .where(table.c.owner_user_id == tag)
                    .values(availability="unavailable")
                )
                assert result.rowcount == 1
                assert await session.scalar(
                    sa.select(table.c.artifact_id).where(table.c.owner_user_id == tag)
                ) == str(run_ids[0])
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# Reuse the migration suite's dedicated per-test DB so durable append-only
# source facts can be retained for the test without weakening their guards.


@pytest.fixture
def producer_authority(isolated_database):  # noqa: F811 - imported pytest fixture
    from alembic import command
    from app.infrastructure.execution.models import (
        ExecutionActivityTaskORM,
        ExecutionEventORM,
        ExecutionStreamOwnerORM,
    )

    engine, config = isolated_database
    # F01 source-authority contract predates F05 receipt-authorized binding.
    command.upgrade(config, "0002execution_view")
    settings = load_deployment_settings()
    identities = [
        {
            "run": uuid4(),
            "activity": uuid4(),
            "event": uuid4(),
            "invocation": uuid4(),
            "step": f"step-{i}",
            "attempt": f"attempt-{i}",
            "owner": "owner-a" if i < 2 else "owner-b",
        }
        for i in range(3)
    ]
    with engine.begin() as connection:
        configure_sync_system_authorization(
            connection,
            actor="f01-reference-seed",
            signing_secret=settings.database_authorization_signing_secret,
        )
        for owner in ("owner-a", "owner-b"):
            connection.execute(
                sa.text("INSERT INTO users(id,email,username) VALUES(:owner,:email,:owner)"),
                {"owner": owner, "email": owner + "@example.test"},
            )
        for index, item in enumerate(identities):
            run_id = item["run"]
            connection.execute(
                sa.text("INSERT INTO sessions(id,owner_user_id) VALUES(:id,:owner)"),
                {"id": str(run_id), "owner": item["owner"]},
            )
            connection.execute(
                sa.text("INSERT INTO artifacts(id,session_id) VALUES(:id,:id)"), {"id": str(run_id)}
            )
            connection.execute(
                metadata.tables["execution_view_runs"].insert(), run_values(run_id, item["owner"])
            )
            connection.execute(
                ExecutionStreamOwnerORM.__table__.insert(),
                {"stream_type": "run", "stream_id": str(run_id), "owner_user_id": item["owner"]},
            )
            connection.execute(
                ExecutionEventORM.__table__.insert(),
                {
                    "position": index + 1,
                    "event_id": item["event"],
                    "stream_type": "run",
                    "stream_id": str(run_id),
                    "stream_version": 1,
                    "event_type": "ActivityRequested",
                    "event_schema_version": 1,
                    "owner_user_id": item["owner"],
                    "correlation_id": uuid4(),
                    "prev_hash": "0" * 64,
                    "event_hash": "a" * 64,
                },
            )
            connection.execute(
                ExecutionActivityTaskORM.__table__.insert(),
                {
                    "activity_id": item["activity"],
                    "run_id": str(run_id),
                    "aggregate_type": "run",
                    "aggregate_id": str(run_id),
                    "activity_type": "tool",
                    "request_event_position": index + 1,
                    "owner_user_id": item["owner"],
                    "timeout_at": NOW,
                    "request_digest": "digest",
                },
            )
            connection.execute(
                metadata.tables["execution_view_steps"].insert(),
                {
                    "id": uuid4(),
                    "run_id": run_id,
                    "step_id": item["step"],
                    "activity_id": item["activity"],
                    "attempt_id": item["attempt"],
                    "invocation_id": item["invocation"],
                    "owner_user_id": item["owner"],
                    "created_by": "f01-test",
                    "relationship": "direct",
                    "kind": "tool",
                    "status": "completed",
                    "projection_revision": 1,
                    "observed_order": 0,
                    "completeness": {},
                },
            )
    return engine.url.database, identities


def producer_values(item):
    return {
        "id": uuid4(),
        "artifact_id": str(item["run"]),
        "version": 1,
        "producer_identity": "producer",
        "producer_run_id": item["run"],
        "producer_step_ids": [item["step"]],
        "activity_id": item["activity"],
        "attempt_id": item["attempt"],
        "invocation_id": item["invocation"],
        "produced_event_id": item["event"],
        "owner_user_id": item["owner"],
        "created_by": "f01-test",
        "evidence_kind": "direct",
        "binding_status": "bound",
        "availability": "available",
        "revision": 1,
    }


def producer_engine(database):
    return create_async_engine(
        sa.engine.make_url(load_deployment_settings().sqlalchemy_database_uri).set(
            database=database
        )
    )


@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("produced_event_id", "event"),
        ("activity_id", "activity"),
        ("producer_step_ids", "step"),
        ("attempt_id", "attempt"),
        ("invocation_id", "invocation"),
    ],
)
@pytest.mark.parametrize("other_index", [1, 2], ids=["other-run", "other-owner"])
def test_provenance_rejects_mismatched_producer_authority(
    producer_authority, field, key, other_index
):
    database, identities = producer_authority

    async def scenario():
        engine = producer_engine(database)
        try:
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session, AuthorizationContext.for_principal(Principal(user_id="owner-a"))
                )
                values = producer_values(identities[0])
                values[field] = (
                    [identities[other_index][key]]
                    if key == "step"
                    else identities[other_index][key]
                )
                with pytest.raises(DBAPIError, match="producer reference mismatch"):
                    await session.execute(
                        metadata.tables["artifact_version_provenance"].insert(), values
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("producer_run_id", "run"),
        ("produced_event_id", "event"),
        ("activity_id", "activity"),
        ("producer_step_ids", "step"),
        ("attempt_id", "attempt"),
        ("invocation_id", "invocation"),
    ],
)
def test_deleted_artifact_does_not_allow_producer_rebinding(producer_authority, field, key):
    database, identities = producer_authority

    async def scenario():
        engine = producer_engine(database)
        table = metadata.tables["artifact_version_provenance"]
        try:
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session, AuthorizationContext.for_principal(Principal(user_id="owner-a"))
                )
                values = producer_values(identities[0])
                await session.execute(table.insert(), values)
                await session.execute(
                    sa.text("DELETE FROM artifacts WHERE id=:id"), {"id": values["artifact_id"]}
                )
                assert (
                    await session.execute(
                        table.update()
                        .where(table.c.id == values["id"])
                        .values(availability="unavailable")
                    )
                ).rowcount == 1
                change = [identities[1][key]] if key == "step" else identities[1][key]
                with pytest.raises(DBAPIError, match="artifact scope mismatch"):
                    await session.execute(
                        table.update().where(table.c.id == values["id"]).values(**{field: change})
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("restricted_definer", [False, True])
def test_valid_pending_and_bound_producers_and_signed_gate(producer_authority, restricted_definer):
    database, identities = producer_authority
    if restricted_definer:
        admin = sa.create_engine(
            sa.engine.make_url(
                sqlalchemy_sync_migration_database_uri(load_deployment_settings())
            ).set(database=database)
        )
        role = os.environ["POSTGRES_MIGRATION_USER"]
        try:
            with admin.begin() as connection:
                assert connection.execute(
                    sa.text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=:role"),
                    {"role": role},
                ).one() == (False, False)
                quoted = connection.dialect.identifier_preparer.quote(role)
                connection.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {quoted}"))
                connection.execute(
                    sa.text(
                        f"GRANT SELECT ON public.artifacts,public.sessions,public.execution_events,public.execution_activity_tasks,public.execution_activity_projection,public.execution_view_steps TO {quoted}"
                    )
                )
                connection.execute(
                    sa.text(
                        f"ALTER FUNCTION public.opencitadel_validate_provenance_scope() OWNER TO {quoted}"
                    )
                )
        finally:
            admin.dispose()

    async def scenario():
        engine = producer_engine(database)
        table = metadata.tables["artifact_version_provenance"]
        try:
            async with factory(engine)() as session:
                await configure_session_authorization(
                    session, AuthorizationContext.for_principal(Principal(user_id="owner-a"))
                )
                values = producer_values(identities[0])
                await session.execute(table.insert(), values)
                pending = producer_values(identities[0]) | {
                    "producer_identity": "pending",
                    "binding_status": "pending",
                }
                for field in (
                    "producer_run_id",
                    "producer_step_ids",
                    "activity_id",
                    "attempt_id",
                    "invocation_id",
                    "produced_event_id",
                ):
                    pending[field] = None
                await session.execute(table.insert(), pending)
                assert await session.scalar(sa.select(sa.func.count()).select_from(table)) == 2
                await session.execute(
                    table.update()
                    .where(table.c.id == pending["id"])
                    .values(
                        **{k: v for k, v in values.items() if k not in ("id", "producer_identity")}
                    )
                )
            for forged in (False, True):
                async with factory(engine)() as session:
                    await configure_session_authorization(session, AuthorizationContext.anonymous())
                    if forged:
                        await session.execute(
                            sa.text(
                                "SELECT set_config('app.auth_mode','user',true),set_config('app.user_id','owner-a',true)"
                            )
                        )
                    with pytest.raises(DBAPIError):
                        await session.execute(table.insert(), producer_values(identities[0]))
            async with factory(engine)() as session:
                for name in ("execution_events", "execution_activity_tasks"):
                    assert not await session.scalar(
                        sa.text("SELECT has_table_privilege(current_user,:name,'SELECT')"),
                        {"name": name},
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
