import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.events import NewEvent, StoredEvent
from app.domain.execution.store import (
    AppendContext,
    CorruptEventStreamError,
    OptimisticConcurrencyError,
    PayloadTooLargeError,
    StreamOwnerScopeMismatchError,
    StreamRef,
    calculate_event_hash,
    verify_stream,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.infrastructure.execution.models import ExecutionEventORM
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
)

NOW = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)


def metric_sample(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def new_event(event_type: str, **payload: object) -> NewEvent:
    return NewEvent(
        event_type=event_type,
        event_schema_version=1,
        public_payload=payload,
        internal_payload={},
    )


def append_context(
    *,
    owner_user_id: str | None = "event-user-a",
    team_id: str | None = None,
) -> AppendContext:
    return AppendContext(
        owner_user_id=owner_user_id,
        team_id=team_id,
        correlation_id=uuid4(),
        causation_id=uuid4(),
        occurred_at=NOW,
    )


async def cleanup_stream(session_factory, stream: StreamRef) -> None:
    del session_factory
    async with execution_admin_session() as session:
        await session.execute(
            text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            text(
                "DELETE FROM execution_events "
                "WHERE stream_type = :stream_type AND stream_id = :stream_id"
            ),
            {"stream_type": stream.stream_type, "stream_id": stream.stream_id},
        )
        await session.execute(
            text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            text(
                "ALTER TABLE execution_stream_owners "
                "DISABLE TRIGGER execution_stream_owners_immutable"
            )
        )
        await session.execute(
            text(
                "DELETE FROM execution_stream_owners "
                "WHERE stream_type = :stream_type AND stream_id = :stream_id"
            ),
            {"stream_type": stream.stream_type, "stream_id": stream.stream_id},
        )
        await session.execute(
            text(
                "ALTER TABLE execution_stream_owners "
                "ENABLE TRIGGER execution_stream_owners_immutable"
            )
        )
        await session.commit()


@pytest.fixture
async def postgres_store(_db_schema):
    engine = create_async_engine(execution_kernel_database_uri())
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_append_version_zero_and_multi_event_versions_are_contiguous(
    postgres_store,
) -> None:
    stream = StreamRef(stream_type="synthetic_run", stream_id=f"append-{uuid4()}")
    try:
        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-test"),
            )
            result = await PostgresEventStore(session).append(
                stream,
                expected_version=0,
                events=(
                    new_event("SyntheticRunRequested", order=1),
                    new_event("SyntheticRunStarted", order=2),
                ),
                context=append_context(),
            )
            await session.commit()

        assert [event.stream_version for event in result.events] == [1, 2]
        assert result.events[1].prev_hash == result.events[0].event_hash
        assert result.first_position < result.last_position

        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-test"),
            )
            loaded = await PostgresEventStore(session).load_stream(
                stream.stream_type,
                stream.stream_id,
            )
        assert loaded == result.events
        verify_stream(loaded)
    finally:
        await cleanup_stream(postgres_store, stream)


@pytest.mark.asyncio
async def test_concurrent_same_expected_version_has_exactly_one_winner(
    postgres_store,
) -> None:
    stream = StreamRef(stream_type="synthetic_run", stream_id=f"race-{uuid4()}")

    async def append_once(marker: str) -> str:
        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system(f"event-store-race-{marker}"),
            )
            try:
                await PostgresEventStore(session).append(
                    stream,
                    expected_version=0,
                    events=(new_event("SyntheticRunRequested", marker=marker),),
                    context=append_context(),
                )
                await session.commit()
                return "accepted"
            except OptimisticConcurrencyError:
                await session.rollback()
                return "conflict"

    try:
        outcomes = await asyncio.gather(append_once("a"), append_once("b"))

        assert sorted(outcomes) == ["accepted", "conflict"]
    finally:
        await cleanup_stream(postgres_store, stream)


@pytest.mark.asyncio
async def test_rls_filters_stream_loads_by_frozen_owner_scope(postgres_store) -> None:
    personal = StreamRef(stream_type="synthetic_run", stream_id=f"personal-{uuid4()}")
    team = StreamRef(stream_type="synthetic_run", stream_id=f"team-{uuid4()}")
    try:
        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-seed"),
            )
            repository = PostgresEventStore(session)
            await repository.append(
                personal,
                0,
                (new_event("SyntheticRunRequested"),),
                append_context(owner_user_id="event-user-a"),
            )
            await repository.append(
                team,
                0,
                (new_event("SyntheticRunRequested"),),
                append_context(owner_user_id=None, team_id="event-team-a"),
            )
            await session.commit()

        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.for_principal(Principal(user_id="event-user-a")),
            )
            await session.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
            repository = PostgresEventStore(session)
            assert len(await repository.load_stream("synthetic_run", personal.stream_id)) == 1
            assert await repository.load_stream("synthetic_run", team.stream_id) == ()
    finally:
        await cleanup_stream(postgres_store, personal)
        await cleanup_stream(postgres_store, team)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        (
            append_context(owner_user_id="owner-a"),
            append_context(owner_user_id="owner-b"),
        ),
        (
            append_context(owner_user_id="owner-a"),
            append_context(owner_user_id=None, team_id="team-a"),
        ),
        (
            append_context(owner_user_id=None, team_id="team-a"),
            append_context(owner_user_id=None, team_id="team-b"),
        ),
    ],
)
async def test_append_rejects_owner_scope_changes_for_an_existing_stream(
    postgres_store,
    original: AppendContext,
    replacement: AppendContext,
) -> None:
    stream = StreamRef(stream_type="synthetic_run", stream_id=f"scope-{uuid4()}")
    try:
        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-scope-seed"),
            )
            store = PostgresEventStore(session)
            await store.append(
                stream,
                0,
                (new_event("SyntheticRunRequested"),),
                original,
            )
            await session.commit()

        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-scope-attack"),
            )
            with pytest.raises(StreamOwnerScopeMismatchError):
                await PostgresEventStore(session).append(
                    stream,
                    1,
                    (new_event("SyntheticRunStarted"),),
                    replacement,
                )
            await session.rollback()

        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-scope-verify"),
            )
            loaded = await PostgresEventStore(session).load_stream(
                stream.stream_type,
                stream.stream_id,
            )
            assert len(loaded) == 1
            assert (loaded[0].owner_user_id, loaded[0].team_id) == (
                original.owner_user_id,
                original.team_id,
            )
    finally:
        await cleanup_stream(postgres_store, stream)


@pytest.mark.asyncio
async def test_database_rejects_direct_cross_owner_append(postgres_store) -> None:
    stream = StreamRef(stream_type="synthetic_run", stream_id=f"db-scope-{uuid4()}")
    try:
        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-db-owner-seed"),
            )
            original = (
                await PostgresEventStore(session).append(
                    stream,
                    0,
                    (new_event("SyntheticRunRequested"),),
                    append_context(owner_user_id="owner-a"),
                )
            ).events[0]
            await session.commit()

        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-db-owner-attack"),
            )
            alien = StoredEvent(
                position=original.position + 1,
                event_id=uuid4(),
                stream_type=stream.stream_type,
                stream_id=stream.stream_id,
                stream_version=2,
                event_type="SyntheticRunStarted",
                event_schema_version=1,
                public_payload={},
                internal_payload={},
                secret_ref=None,
                owner_user_id="owner-b",
                team_id=None,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                occurred_at=NOW,
                prev_hash=original.event_hash,
                event_hash="0" * 64,
            )
            alien = alien.model_copy(update={"event_hash": calculate_event_hash(alien)})
            session.add(
                ExecutionEventORM(
                    event_id=alien.event_id,
                    stream_type=alien.stream_type,
                    stream_id=alien.stream_id,
                    stream_version=alien.stream_version,
                    event_type=alien.event_type,
                    event_schema_version=alien.event_schema_version,
                    public_payload=alien.public_payload,
                    internal_payload=alien.internal_payload,
                    secret_ref=alien.secret_ref,
                    owner_user_id=alien.owner_user_id,
                    team_id=alien.team_id,
                    correlation_id=alien.correlation_id,
                    causation_id=alien.causation_id,
                    occurred_at=alien.occurred_at,
                    prev_hash=alien.prev_hash,
                    event_hash=alien.event_hash,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
    finally:
        await cleanup_stream(postgres_store, stream)


@pytest.mark.asyncio
async def test_append_rejects_payload_over_default_64_kib(postgres_store) -> None:
    stream = StreamRef(stream_type="synthetic_run", stream_id=f"large-{uuid4()}")
    async with postgres_store() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("event-store-large-payload"),
        )
        with pytest.raises(PayloadTooLargeError):
            await PostgresEventStore(session).append(
                stream,
                0,
                (new_event("SyntheticRunRequested", content="x" * (64 * 1024)),),
                append_context(),
            )


@pytest.mark.asyncio
async def test_privileged_sql_tamper_is_detected_by_hash_verification(
    postgres_store,
) -> None:
    stream = StreamRef(stream_type="synthetic_run", stream_id=f"tamper-{uuid4()}")
    try:
        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-tamper-seed"),
            )
            await PostgresEventStore(session).append(
                stream,
                0,
                (new_event("SyntheticRunRequested", original=True),),
                append_context(),
            )
            await session.commit()

        async with execution_admin_session() as session:
            await session.execute(
                text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
            )
            await session.execute(
                text(
                    "UPDATE execution_events SET public_payload = "
                    "'{\"tampered\": true}'::jsonb "
                    "WHERE stream_type = :stream_type AND stream_id = :stream_id"
                ),
                {"stream_type": stream.stream_type, "stream_id": stream.stream_id},
            )
            await session.execute(
                text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
            )
            await session.commit()

        async with postgres_store() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("event-store-tamper-read"),
            )
            labels = {"reason": "event_hash_mismatch"}
            before_failures = metric_sample(
                "execution_replay_failures_total",
                labels,
            )
            with pytest.raises(CorruptEventStreamError):
                await PostgresEventStore(session).load_stream(
                    stream.stream_type,
                    stream.stream_id,
                )
            assert metric_sample("execution_replay_failures_total", labels) - before_failures == 1
    finally:
        await cleanup_stream(postgres_store, stream)
