from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.outbox_dispatcher import OutboxDispatcher
from app.domain.execution.events import NewEvent
from app.domain.execution.store import AppendContext, StreamRef
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.adapters.execution_ports import SqlAlchemyOutboxStore
from app.infrastructure.adapters.redis_capabilities import RedisWakeupAdapter
from app.infrastructure.execution.models import (
    ExecutionEventORM,
    ExecutionOutboxORM,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
)

NOW = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self, *, fail_after_publish: bool = False) -> None:
        self.messages: list = []
        self.fail_after_publish = fail_after_publish

    async def publish(self, message) -> None:
        self.messages.append(message)
        if self.fail_after_publish:
            raise RuntimeError("injected crash after publish")


async def seed_outbox(session_factory) -> tuple[StreamRef, object]:
    stream = StreamRef(stream_type="synthetic_run", stream_id=f"outbox-{uuid4()}")
    outbox_id = uuid4()
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("outbox-test-seed"),
        )
        appended = await PostgresEventStore(session).append(
            stream,
            0,
            (
                NewEvent(
                    event_type="SyntheticRunRequested",
                    event_schema_version=1,
                    public_payload={},
                    internal_payload={},
                ),
            ),
            AppendContext(
                owner_user_id="outbox-user",
                team_id=None,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                occurred_at=NOW,
            ),
        )
        session.add(
            ExecutionOutboxORM(
                outbox_id=outbox_id,
                event_position=appended.events[0].position,
                destination="execution.events",
                dedupe_key=f"event:{appended.events[0].event_id}",
                owner_user_id="outbox-user",
                team_id=None,
                available_at=NOW,
            )
        )
        await session.commit()
    return stream, outbox_id


async def cleanup_outbox(session_factory, stream: StreamRef, outbox_id) -> None:
    del session_factory
    async with execution_admin_session() as session:
        await session.execute(
            delete(ExecutionOutboxORM).where(ExecutionOutboxORM.outbox_id == outbox_id)
        )
        await session.execute(
            text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            delete(ExecutionEventORM).where(ExecutionEventORM.stream_id == stream.stream_id)
        )
        await session.execute(
            text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
        )
        await session.commit()


@pytest.fixture
async def outbox_database(_db_schema):
    engine = create_async_engine(execution_kernel_database_uri())
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    resources: list[tuple[StreamRef, object]] = []
    try:
        yield session_factory, resources
    finally:
        for stream, outbox_id in resources:
            await cleanup_outbox(session_factory, stream, outbox_id)
        await engine.dispose()


def dispatcher(session_factory, publisher) -> OutboxDispatcher:
    return OutboxDispatcher(
        store=SqlAlchemyOutboxStore(
            session_factory=session_factory,
            authorization=AuthorizationContext.system("outbox-test"),
        ),
        publisher=publisher,
        claim_ttl=timedelta(seconds=5),
        base_retry_delay=timedelta(seconds=1),
    )


@pytest.mark.asyncio
async def test_successful_outbox_delivery_is_not_republished(
    outbox_database,
) -> None:
    session_factory, resources = outbox_database
    stream, outbox_id = await seed_outbox(session_factory)
    resources.append((stream, outbox_id))
    publisher = RecordingPublisher()
    service = dispatcher(session_factory, publisher)

    first = await service.dispatch_batch(limit=10, now=NOW)
    duplicate_scan = await service.dispatch_batch(
        limit=10,
        now=NOW + timedelta(seconds=1),
    )

    assert first.published == 1
    assert first.failed == 0
    assert duplicate_scan.claimed == 0
    assert len(publisher.messages) == 1
    message = publisher.messages[0]
    assert set(vars(message)) == {
        "destination",
        "dedupe_key",
        "event_position",
    }


@pytest.mark.asyncio
async def test_crash_after_publish_retries_with_the_same_dedupe_hint(
    outbox_database,
) -> None:
    session_factory, resources = outbox_database
    stream, outbox_id = await seed_outbox(session_factory)
    resources.append((stream, outbox_id))
    publisher = RecordingPublisher(fail_after_publish=True)
    service = dispatcher(session_factory, publisher)

    first = await service.dispatch_batch(limit=10, now=NOW)
    publisher.fail_after_publish = False
    retried = await service.dispatch_batch(
        limit=10,
        now=NOW + timedelta(seconds=2),
    )

    assert first.failed == 1
    assert retried.published == 1
    assert len(publisher.messages) == 2
    assert publisher.messages[0] == publisher.messages[1]


@pytest.mark.asyncio
async def test_failure_before_publish_is_retried_from_postgres(
    outbox_database,
) -> None:
    session_factory, resources = outbox_database
    stream, outbox_id = await seed_outbox(session_factory)
    resources.append((stream, outbox_id))

    class FailBeforePublish:
        async def publish(self, message) -> None:
            raise ConnectionError("injected outage before publish")

    failed = await dispatcher(
        session_factory,
        FailBeforePublish(),
    ).dispatch_batch(limit=10, now=NOW)
    publisher = RecordingPublisher()
    recovered = await dispatcher(
        session_factory,
        publisher,
    ).dispatch_batch(limit=10, now=NOW + timedelta(seconds=2))

    assert failed.failed == 1
    assert recovered.published == 1
    assert len(publisher.messages) == 1


@pytest.mark.asyncio
async def test_redis_flush_after_unacknowledged_publish_is_recovered_from_db(
    outbox_database,
) -> None:
    session_factory, resources = outbox_database
    stream, outbox_id = await seed_outbox(session_factory)
    resources.append((stream, outbox_id))
    settings = load_deployment_settings()
    client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        decode_responses=True,
    )
    wakeup = RedisWakeupAdapter(client)

    class PublishThenCrash:
        async def publish(self, message) -> None:
            await wakeup.publish(message)
            raise RuntimeError("crash before outbox acknowledgement")

    try:
        await client.flushdb()
        failed = await dispatcher(
            session_factory,
            PublishThenCrash(),
        ).dispatch_batch(limit=10, now=NOW)
        assert failed.failed == 1
        assert await client.xlen(RedisWakeupAdapter.STREAM_KEY) == 1

        await client.flushdb()
        recovered = await dispatcher(
            session_factory,
            wakeup,
        ).dispatch_batch(
            limit=10,
            now=NOW + timedelta(seconds=2),
        )
        assert recovered.published == 1
        assert await client.xlen(RedisWakeupAdapter.STREAM_KEY) == 1
        entries = await client.xrange(RedisWakeupAdapter.STREAM_KEY)
        assert set(entries[0][1]) == {
            "destination",
            "dedupe_key",
            "event_position",
        }
    finally:
        await client.flushdb()
        await client.aclose()
