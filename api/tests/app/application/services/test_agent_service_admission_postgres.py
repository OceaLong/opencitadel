import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.public_projection import (
    PublicEventPage,
    PublicExecutionEvent,
)
from app.application.services.agent_service import AgentService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
)


class BlockingAdmission:
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        self.calls: list[dict] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def admit(self, **kwargs) -> UUID:
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        return self.run_id


class TerminalProjection:
    async def list_events(self, *, run_id=None, **_kwargs) -> PublicEventPage:
        if run_id is None:
            return PublicEventPage(
                events=(),
                next_cursor=None,
                prev_cursor=None,
                has_earlier=False,
            )
        return PublicEventPage(
            events=(
                PublicExecutionEvent(
                    cursor="cursor-1",
                    event_id=uuid4(),
                    event_type="done",
                    run_id=run_id,
                    stream_id="session-concurrency",
                    stream_version=1,
                    payload={"status": "completed"},
                    occurred_at=datetime.now(UTC),
                ),
            ),
            next_cursor=None,
            prev_cursor=None,
            has_earlier=False,
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_postgres_session_lock_admits_only_one_parallel_turn() -> None:
    user_id = f"chat-lock-user-{uuid4()}"
    session_id = f"chat-lock-session-{uuid4()}"
    first_request_id = uuid4()
    second_request_id = uuid4()
    admitted_run_id = uuid4()
    scope = OwnerScope.personal(user_id)
    authorization = AuthorizationContext.for_principal(
        Principal(user_id=user_id),
        scope=scope,
        request_id="chat-lock-test",
    )
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=settings.session_secret,
    )
    admission = BlockingAdmission(admitted_run_id)
    service = AgentService(
        uow_factory=lambda: DBUnitOfWork(
            session_factory,
            secret_cipher=ApiKeyCipher(settings.api_key_secret),
            audit_signing_key=settings.audit_signing_key,
            audit_signing_key_id=settings.audit_signing_key_id,
            database_authorization_signing_secret=settings.session_secret,
            authorization_context=authorization,
        ),
        admission_service=admission,
        command_ingress=object(),
        public_projection=TerminalProjection(),
        run_projection=object(),
        poll_interval_seconds=0.001,
        idle_timeout_seconds=0.01,
    )

    async with execution_admin_session() as admin:
        await admin.execute(
            text("INSERT INTO users (id, email, username) VALUES (:id, :email, :username)"),
            {
                "id": user_id,
                "email": f"{user_id}@example.test",
                "username": user_id,
            },
        )
        await admin.execute(
            text(
                "INSERT INTO sessions (id, owner_user_id, status) "
                "VALUES (:id, :owner_user_id, 'pending')"
            ),
            {"id": session_id, "owner_user_id": user_id},
        )
        await admin.commit()

    async def consume(request_id: UUID, message: str):
        return [
            event
            async for event in service.chat(
                session_id,
                owner_scope=scope,
                message=message,
                request_id=request_id,
            )
        ]

    first = asyncio.create_task(consume(first_request_id, "first"))
    second = None
    try:
        await asyncio.wait_for(admission.started.wait(), timeout=2)
        second = asyncio.create_task(consume(second_request_id, "second"))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), timeout=0.05)

        admission.release.set()
        assert [event.event_type for event in await first] == ["done"]
        with pytest.raises(ValueError, match="already has an active Run"):
            await second

        assert len(admission.calls) == 1
        async with execution_admin_session() as admin:
            row = (
                await admin.execute(
                    text(
                        "SELECT active_execution_run_id, "
                        "active_execution_request_id FROM sessions WHERE id = :id"
                    ),
                    {"id": session_id},
                )
            ).one()
        assert row.active_execution_run_id == admitted_run_id
        assert row.active_execution_request_id == first_request_id
    finally:
        admission.release.set()
        if not first.done():
            first.cancel()
        if second is not None and not second.done():
            second.cancel()
        await asyncio.gather(
            first,
            *((second,) if second is not None else ()),
            return_exceptions=True,
        )
        async with execution_admin_session() as admin:
            await admin.execute(
                text("DELETE FROM sessions WHERE id = :id"),
                {"id": session_id},
            )
            await admin.execute(
                text("DELETE FROM users WHERE id = :id"),
                {"id": user_id},
            )
            await admin.commit()
        await engine.dispose()
