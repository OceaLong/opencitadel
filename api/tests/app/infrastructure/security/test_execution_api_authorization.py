"""Production execution services must bind the ambient request identity."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.execution.command_ingress import CommandIngress
from app.application.execution.public_projection import PublicEventCursor
from app.application.security.authorization_context import authorization_scope
from app.domain.execution.commands import CommandContext, RegisteredCommand
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.adapters.execution_ports import SqlAlchemyCommandEnvelopeWriter
from app.infrastructure.execution.models import (
    ExecutionCommandInboxORM,
    ExecutionPublicEventORM,
)
from app.infrastructure.execution.postgres_inbox_source import PostgresInboxSource
from app.infrastructure.execution.postgres_public_projection import PostgresPublicProjection
from app.infrastructure.execution.sqlalchemy_orchestrator import (
    SqlAlchemyExecutionOrchestrator,
)
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import DeploymentSettings, load_deployment_settings
from tests.app.execution_test_support import (
    execution_admin_session,
    execution_kernel_database_uri,
)


def _sessions(engine, settings: DeploymentSettings):
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        info={"database_authorization_signing_secret": settings.session_secret},
    )


def _command_ingress(session_factory) -> CommandIngress:
    return CommandIngress(
        writer=SqlAlchemyCommandEnvelopeWriter(
            session_factory=session_factory,
            authorization=None,
        )
    )


def _public_projection(
    session_factory,
    settings: DeploymentSettings,
) -> PostgresPublicProjection:
    return PostgresPublicProjection(
        session_factory=session_factory,
        authorization=None,
        cursor=PublicEventCursor(secret=hashlib.sha256(settings.api_key_secret.encode()).digest()),
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_api_command_ingress_cannot_write_another_users_scope() -> None:
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = _sessions(engine, settings)
    ingress = _command_ingress(session_factory)
    command_id = uuid4()
    run_id = uuid4()
    principal = Principal(user_id="api-rls-user-a")
    try:
        with (
            authorization_scope(
                AuthorizationContext.for_principal(
                    principal,
                    scope=OwnerScope.personal(principal.user_id),
                )
            ),
            pytest.raises(DBAPIError),
        ):
            await ingress.submit(
                RegisteredCommand(
                    command_id=command_id,
                    command_type="CreateRun",
                    run_id=run_id,
                    payload={
                        "family": "agent",
                        "source_entity_type": "session",
                        "source_entity_id": "foreign-session",
                        "semantic_payload": {},
                        "public_input": {},
                    },
                ),
                CommandContext(
                    owner_user_id="api-rls-user-b",
                    team_id=None,
                    correlation_id=run_id,
                    causation_id=None,
                    issued_at=datetime(2026, 8, 24, tzinfo=UTC),
                ),
            )
    finally:
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionCommandInboxORM).where(
                    ExecutionCommandInboxORM.command_id == command_id
                )
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_api_public_projection_cannot_read_another_users_scope() -> None:
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = _sessions(engine, settings)
    projection = _public_projection(session_factory, settings)
    event_id = uuid4()
    run_id = uuid4()
    position = 10_000_000_000 + (event_id.int % 2_000_000_000)
    async with execution_admin_session() as session:
        session.add(
            ExecutionPublicEventORM(
                position=position,
                event_id=event_id,
                run_id=run_id,
                source_entity_type="session",
                source_entity_id="foreign-session",
                stream_type="run",
                stream_id=str(run_id),
                stream_version=1,
                event_type="RunCreated",
                payload={"kind": "message"},
                owner_user_id="api-query-user-b",
                team_id=None,
                occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
            )
        )
        await session.commit()
    principal = Principal(user_id="api-query-user-a")
    try:
        with authorization_scope(
            AuthorizationContext.for_principal(
                principal,
                scope=OwnerScope.personal(principal.user_id),
            )
        ):
            page = await projection.list_events(
                source_entity_type="session",
                source_entity_id="foreign-session",
                owner_scope=OwnerScope.personal("api-query-user-b"),
            )
        assert page.events == ()
    finally:
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionPublicEventORM).where(ExecutionPublicEventORM.event_id == event_id)
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_api_role_cannot_forge_system_or_admin_rls_claims() -> None:
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = _sessions(engine, settings)
    event_id = uuid4()
    run_id = uuid4()
    position = 12_000_000_000 + (event_id.int % 2_000_000_000)
    async with execution_admin_session() as session:
        session.add(
            ExecutionPublicEventORM(
                position=position,
                event_id=event_id,
                run_id=run_id,
                source_entity_type="session",
                source_entity_id="forged-session",
                stream_type="run",
                stream_id=str(run_id),
                stream_version=1,
                event_type="RunCreated",
                payload={"kind": "message"},
                owner_user_id="rls-forgery-victim",
                team_id=None,
                occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
            )
        )
        await session.commit()
    try:
        async with session_factory() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.for_principal(
                    Principal(user_id="rls-forgery-attacker"),
                    scope=OwnerScope.personal("rls-forgery-attacker"),
                ),
            )
            await session.execute(
                text(
                    "SELECT set_config('app.auth_mode', 'system', true), "
                    "set_config('app.is_admin', 'true', true), "
                    "set_config('app.user_id', 'rls-forgery-victim', true)"
                )
            )
            visible = await session.scalar(
                select(func.count())
                .select_from(ExecutionPublicEventORM)
                .where(ExecutionPublicEventORM.event_id == event_id)
            )
            assert visible == 0
            await session.rollback()
    finally:
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionPublicEventORM).where(ExecutionPublicEventORM.event_id == event_id)
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_oversized_api_command_is_reloaded_and_rejected_by_kernel() -> None:
    settings = load_deployment_settings()
    app_engine = create_async_engine(settings.sqlalchemy_database_uri)
    app_sessions = _sessions(app_engine, settings)
    kernel_engine = create_async_engine(execution_kernel_database_uri())
    kernel_sessions = _sessions(kernel_engine, settings)
    ingress = _command_ingress(app_sessions)
    command_id = uuid4()
    run_id = uuid4()
    owner_user_id = "oversized-command-owner"
    issued_at = datetime(2026, 8, 24, 12, tzinfo=UTC)
    principal = Principal(user_id=owner_user_id)
    try:
        with authorization_scope(
            AuthorizationContext.for_principal(
                principal,
                scope=OwnerScope.personal(owner_user_id),
            )
        ):
            await ingress.submit(
                RegisteredCommand(
                    command_id=command_id,
                    command_type="CreateRun",
                    run_id=run_id,
                    payload={"content": "x" * (64 * 1024)},
                ),
                CommandContext(
                    owner_user_id=owner_user_id,
                    team_id=None,
                    correlation_id=run_id,
                    causation_id=None,
                    issued_at=issued_at,
                ),
            )

        source = PostgresInboxSource(
            session_factory=kernel_sessions,
            authorization=AuthorizationContext.system("oversized-command-source"),
        )
        pending = await source.load_pending(now=issued_at, limit=100)
        reloaded = next(item for item in pending if item.command_id == command_id)
        assert reloaded.payload == {}
        assert reloaded.payload_digest is not None

        result = await SqlAlchemyExecutionOrchestrator(
            session_factory=kernel_sessions,
            aggregates={"run": RunAggregate()},
            authorization=AuthorizationContext.system("oversized-command-kernel"),
            now=lambda: issued_at,
        ).handle(reloaded)

        assert result.status == "rejected"
        assert result.rejection_code == "PAYLOAD_TOO_LARGE"
        async with execution_admin_session() as session:
            row = await session.get(ExecutionCommandInboxORM, command_id)
            assert row is not None
            assert row.status == "rejected"
            assert row.payload == {}
            assert row.payload_digest == reloaded.payload_digest
            assert row.rejection_code == "PAYLOAD_TOO_LARGE"
    finally:
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionCommandInboxORM).where(
                    ExecutionCommandInboxORM.command_id == command_id
                )
            )
            await session.commit()
        await app_engine.dispose()
        await kernel_engine.dispose()
