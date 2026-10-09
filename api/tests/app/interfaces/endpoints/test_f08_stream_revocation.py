"""Already-open SSE must honor current persisted membership and user security state."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.composition.execution_content import build_execution_event_service
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.team import TeamRole
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.interfaces.endpoints.execution_view_routes import stream_events
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_artifact_provenance_postgres import seed
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.execution_test_support import execution_admin_session, run_policy_snapshot_json

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest_asyncio.fixture
async def api_sessions(isolated_database):  # noqa: F811
    admin, _ = isolated_database
    settings = load_deployment_settings()
    engine = create_async_engine(
        make_url(settings.sqlalchemy_database_uri).set(
            drivername="postgresql+asyncpg", database=admin.url.database
        )
    )
    try:
        yield async_sessionmaker(
            engine,
            info={
                "database_authorization_signing_secret": settings.database_authorization_signing_secret
            },
        )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("stage", ["buffered", "poll"])
@pytest.mark.parametrize("revocation", ["membership", "disabled", "token"])
async def test_open_team_stream_stops_after_persisted_revocation(revocation, stage, api_sessions):
    from datetime import UTC, datetime

    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunAggregate
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator

    user, session = await seed()
    team, run = uuid4().hex, uuid4()
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO teams(id,name) VALUES (:id,'stream team')"), {"id": team}
        )
        await db.execute(
            text("INSERT INTO team_members(team_id,user_id,role) VALUES (:team,:user,'member')"),
            {"team": team, "user": user},
        )
        await db.execute(
            text("UPDATE sessions SET team_id=:team,owner_user_id=NULL WHERE id=:session"),
            {"team": team, "session": session},
        )
        await db.commit()
    scope = OwnerScope.team(user, team)
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=execution_admin_session,
        aggregates={"run": RunAggregate()},
        authorization=AuthorizationContext.system("f08-revocation-fixture"),
    )

    async def command(kind, payload):
        result = await handler.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=kind,
                command_schema_version=1,
                stream_type="run",
                stream_id=str(run),
                owner_user_id=None,
                team_id=team,
                correlation_id=run,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )
        assert result.status == "accepted"

    await command(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": session,
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )
    await command("StartRun", {})
    projector = PostgresFormalProjector(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f08-revocation-fixture"),
    )
    await projector.run_once(scope, limit=100)
    principal = Principal(user_id=user, team_roles={team: TeamRole.MEMBER})
    ctx = WorkspaceContext(principal=principal, scope=scope)
    service = build_execution_event_service(
        settings=load_deployment_settings(),
        resources=SimpleNamespace(postgres=SimpleNamespace(session_factory=api_sessions)),
        authorization=AuthorizationContext.for_principal(principal, scope=scope),
    )
    initial_count = len((await service.list_events(scope, run)).events)
    assert initial_count > 1

    class Connected:
        async def is_disconnected(self):
            return False

    stream = await stream_events(
        Connected(), run, after=None, last_event_id=None, ctx=ctx, service=service
    )
    try:
        first = await anext(stream.body_iterator)
        assert first.event == "execution"
        if stage == "poll":
            for _ in range(initial_count - 1):
                assert (await anext(stream.body_iterator)).event == "execution"
        async with execution_admin_session() as db:
            if revocation == "membership":
                await db.execute(
                    text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
                    {"team": team, "user": user},
                )
            elif revocation == "disabled":
                await db.execute(
                    text("UPDATE users SET status='disabled' WHERE id=:user"), {"user": user}
                )
            else:
                await db.execute(
                    text("UPDATE users SET token_version=token_version+1 WHERE id=:user"),
                    {"user": user},
                )
            await db.commit()
        next_event = await anext(stream.body_iterator)
        assert next_event.event == "refresh"
        assert json.loads(next_event.data)["code"] == "permission_denied"
        with pytest.raises(StopAsyncIteration):
            await anext(stream.body_iterator)
    finally:
        await stream.body_iterator.aclose()
