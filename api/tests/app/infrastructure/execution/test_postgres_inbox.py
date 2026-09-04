import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.orchestrator import CommandResult
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.errors import CommandInProgressError
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import ExecutionCommandInboxORM
from app.infrastructure.execution.postgres_inbox import PostgresInbox
from app.infrastructure.execution.postgres_inbox_source import PostgresInboxSource
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
)

NOW = datetime(2026, 8, 20, 13, 0, tzinfo=UTC)


def command(command_id=None, *, payload=None) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id or uuid4(),
        command_type="RequestSyntheticRun",
        command_schema_version=1,
        stream_type="synthetic_run",
        stream_id=f"inbox-{uuid4()}",
        expected_stream_version=None,
        owner_user_id="inbox-user",
        team_id=None,
        correlation_id=uuid4(),
        causation_id=None,
        issued_at=NOW,
        payload=payload or {},
    )


def test_persisted_omitted_payload_digest_survives_worker_reload() -> None:
    payload_bytes = b'{"blob":"' + (b"x" * 70_000) + b'"}'
    digest = f"sha256:{hashlib.sha256(payload_bytes).hexdigest()}"
    candidate = command(payload={"blob": "x" * 70_000})
    record = SimpleNamespace(
        command_id=candidate.command_id,
        command_type=candidate.command_type,
        command_schema_version=candidate.command_schema_version,
        stream_type=candidate.stream_type,
        stream_id=candidate.stream_id,
        expected_stream_version=candidate.expected_stream_version,
        owner_user_id=candidate.owner_user_id,
        team_id=candidate.team_id,
        correlation_id=candidate.correlation_id,
        causation_id=candidate.causation_id,
        issued_at=candidate.issued_at,
        payload={},
        payload_digest=digest,
        payload_ref=None,
    )

    reloaded = PostgresInboxSource._to_command(record)

    assert reloaded.payload == {}
    assert reloaded.payload_digest == digest
    PostgresInbox._assert_same_command(record, reloaded)


def test_idempotent_command_retry_may_have_a_new_transport_timestamp() -> None:
    candidate = command(payload={"message": "one logical turn"})
    record = SimpleNamespace(
        command_type=candidate.command_type,
        command_schema_version=candidate.command_schema_version,
        stream_type=candidate.stream_type,
        stream_id=candidate.stream_id,
        expected_stream_version=candidate.expected_stream_version,
        owner_user_id=candidate.owner_user_id,
        team_id=candidate.team_id,
        correlation_id=candidate.correlation_id,
        causation_id=candidate.causation_id,
        issued_at=candidate.issued_at,
        payload=candidate.payload,
        payload_digest=None,
        payload_ref=None,
    )
    retried = candidate.model_copy(update={"issued_at": candidate.issued_at + timedelta(seconds=1)})

    PostgresInbox._assert_same_command(record, retried)


@pytest.fixture
async def inbox_database(_db_schema):
    engine = create_async_engine(execution_kernel_database_uri())
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    command_ids: list = []
    try:
        yield session_factory, command_ids
    finally:
        async with session_factory() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("inbox-test-cleanup"),
            )
            await session.execute(
                delete(ExecutionCommandInboxORM).where(
                    ExecutionCommandInboxORM.command_id.in_(command_ids)
                )
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_receive_is_idempotent_and_crash_before_claim_remains_eligible(
    inbox_database,
) -> None:
    session_factory, command_ids = inbox_database
    candidate = command()
    command_ids.append(candidate.command_id)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-receive-test"),
        )
        inbox = PostgresInbox(session)
        assert await inbox.receive(candidate) is True
        assert await inbox.receive(candidate) is False
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-claim-test"),
        )
        claim = await PostgresInbox(session).claim(
            candidate,
            now=NOW,
            claim_ttl=timedelta(seconds=30),
        )
        assert claim.status == "claimed"
        assert claim.generation == 1
        await session.rollback()


@pytest.mark.asyncio
async def test_completed_result_is_returned_without_reprocessing(
    inbox_database,
) -> None:
    session_factory, command_ids = inbox_database
    candidate = command()
    command_ids.append(candidate.command_id)
    result = CommandResult(
        command_id=candidate.command_id,
        status="accepted",
        first_event_position=10,
        last_event_position=11,
        rejection_code=None,
    )

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-complete-test"),
        )
        inbox = PostgresInbox(session)
        claim = await inbox.claim(
            candidate,
            now=NOW,
            claim_ttl=timedelta(seconds=30),
        )
        assert claim.status == "claimed"
        await inbox.complete(result, now=NOW)
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-duplicate-test"),
        )
        duplicate = await PostgresInbox(session).claim(
            candidate,
            now=NOW + timedelta(seconds=1),
            claim_ttl=timedelta(seconds=30),
        )
        assert duplicate.status == "completed"
        assert duplicate.result == result


@pytest.mark.asyncio
async def test_load_pending_claims_disjoint_batches_across_workers(inbox_database) -> None:
    session_factory, command_ids = inbox_database
    first_command = command()
    second_command = command()
    command_ids.extend([first_command.command_id, second_command.command_id])

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-disjoint-seed"),
        )
        inbox = PostgresInbox(session)
        await inbox.receive(first_command)
        await inbox.receive(second_command)
        await session.commit()

    source = PostgresInboxSource(
        session_factory=session_factory,
        authorization=AuthorizationContext.system("inbox-disjoint-source"),
        claim_ttl=timedelta(seconds=30),
    )

    # Two sequential polls stand in for two replicas: the second must not
    # re-load the command the first already claimed (marked ``processing``).
    first_batch = await source.load_pending(now=NOW, limit=1)
    second_batch = await source.load_pending(now=NOW, limit=1)

    assert len(first_batch) == 1
    assert len(second_batch) == 1
    assert first_batch[0].command_id != second_batch[0].command_id
    assert {first_batch[0].command_id, second_batch[0].command_id} == {
        first_command.command_id,
        second_command.command_id,
    }


@pytest.mark.asyncio
async def test_concurrent_claim_on_locked_row_is_in_progress(inbox_database) -> None:
    session_factory, command_ids = inbox_database
    candidate = command()
    command_ids.append(candidate.command_id)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-lock-seed"),
        )
        await PostgresInbox(session).receive(candidate)
        await session.commit()

    async with session_factory() as session_a:
        await configure_session_authorization(
            session_a,
            AuthorizationContext.system("inbox-lock-holder"),
        )
        # Hold a row lock without mutating the row (a mutation would make the
        # rival's idempotent receive() block on the uncommitted write rather
        # than exercise SKIP LOCKED).
        locked = await session_a.scalar(
            select(ExecutionCommandInboxORM)
            .where(ExecutionCommandInboxORM.command_id == candidate.command_id)
            .with_for_update()
        )
        assert locked is not None
        # A concurrent claimer must skip the locked row (SKIP LOCKED) and surface
        # a non-fatal CommandInProgressError instead of blocking on the lock.
        async with session_factory() as session_b:
            await configure_session_authorization(
                session_b,
                AuthorizationContext.system("inbox-lock-rival"),
            )
            with pytest.raises(CommandInProgressError):
                await PostgresInbox(session_b).claim(
                    candidate,
                    now=NOW,
                    claim_ttl=timedelta(seconds=30),
                )
            await session_b.rollback()
        await session_a.rollback()


@pytest.mark.asyncio
async def test_expired_processing_claim_advances_generation(inbox_database) -> None:
    session_factory, command_ids = inbox_database
    candidate = command()
    command_ids.append(candidate.command_id)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-expire-test"),
        )
        first = await PostgresInbox(session).claim(
            candidate,
            now=NOW,
            claim_ttl=timedelta(seconds=1),
        )
        assert first.generation == 1
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-reclaim-test"),
        )
        reclaimed = await PostgresInbox(session).claim(
            candidate,
            now=NOW + timedelta(seconds=2),
            claim_ttl=timedelta(seconds=30),
        )
        assert reclaimed.status == "claimed"
        assert reclaimed.generation == 2
        await session.rollback()


@pytest.mark.asyncio
async def test_command_exceeding_claim_attempts_is_dead_lettered(inbox_database) -> None:
    """K2-5: a poison command stops being retried once the claim cap is hit and
    settles as a rejected COMMAND_DEAD_LETTERED result."""
    session_factory, command_ids = inbox_database
    candidate = command()
    command_ids.append(candidate.command_id)

    for attempt in range(1, 3):
        async with session_factory() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("inbox-deadletter-test"),
            )
            claim = await PostgresInbox(session, max_claim_attempts=2).claim(
                candidate,
                now=NOW + timedelta(seconds=attempt * 10),
                claim_ttl=timedelta(seconds=1),
            )
            assert claim.status == "claimed"
            await session.commit()  # crash-before-complete: claim lease expires

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-deadletter-test"),
        )
        final = await PostgresInbox(session, max_claim_attempts=2).claim(
            candidate,
            now=NOW + timedelta(minutes=5),
            claim_ttl=timedelta(seconds=30),
        )
        await session.commit()

    assert final.status == "completed"
    assert final.result is not None
    assert final.result.status == "rejected"
    assert final.result.rejection_code == "COMMAND_DEAD_LETTERED"

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-deadletter-test"),
        )
        record = await session.scalar(
            select(ExecutionCommandInboxORM).where(
                ExecutionCommandInboxORM.command_id == candidate.command_id
            )
        )
        assert record is not None
        assert record.status == "dead_lettered"
        assert record.rejection_code == "COMMAND_DEAD_LETTERED"

        # Dead-lettered rows are terminal: a later claim never reprocesses them.
        again = await PostgresInbox(session, max_claim_attempts=2).claim(
            candidate,
            now=NOW + timedelta(minutes=10),
            claim_ttl=timedelta(seconds=30),
        )
        assert again.status == "completed"
        assert again.result is not None
        assert again.result.rejection_code == "COMMAND_DEAD_LETTERED"
        await session.rollback()


@pytest.mark.asyncio
async def test_purge_completed_deletes_only_old_settled_rows(inbox_database) -> None:
    """K2-5 GC: settled rows older than the cutoff go; pending and fresh stay."""
    session_factory, command_ids = inbox_database
    old_settled = command()
    fresh_settled = command()
    still_pending = command()
    command_ids.extend([old_settled.command_id, fresh_settled.command_id, still_pending.command_id])

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-purge-test"),
        )
        inbox = PostgresInbox(session)
        for candidate in (old_settled, fresh_settled, still_pending):
            await inbox.receive(candidate)
        for candidate, processed_at in (
            (old_settled, NOW - timedelta(days=10)),
            (fresh_settled, NOW - timedelta(hours=1)),
        ):
            claim = await inbox.claim(candidate, now=NOW, claim_ttl=timedelta(seconds=30))
            assert claim.status == "claimed"
            await inbox.complete(
                CommandResult(
                    command_id=candidate.command_id,
                    status="accepted",
                    first_event_position=None,
                    last_event_position=None,
                    rejection_code=None,
                ),
                now=processed_at,
            )
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-purge-test"),
        )
        purged = await PostgresInbox(session).purge_completed(
            before=NOW - timedelta(days=7),
            limit=100,
        )
        await session.commit()
        assert purged == 1

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-purge-test"),
        )
        remaining = set(
            (
                await session.scalars(
                    select(ExecutionCommandInboxORM.command_id).where(
                        ExecutionCommandInboxORM.command_id.in_(
                            [
                                old_settled.command_id,
                                fresh_settled.command_id,
                                still_pending.command_id,
                            ]
                        )
                    )
                )
            ).all()
        )
        assert remaining == {fresh_settled.command_id, still_pending.command_id}


@pytest.mark.asyncio
async def test_batch_preclaim_generations_do_not_consume_the_delivery_budget(
    inbox_database,
) -> None:
    """claim_generation also climbs on the kernel's batch pre-claim; the
    dead-letter cap must count real deliveries (delivery_attempts) only, or
    every kernel delivery would burn two attempts and halve the budget."""
    session_factory, command_ids = inbox_database
    candidate = command()
    command_ids.append(candidate.command_id)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-budget-test"),
        )
        await PostgresInbox(session).receive(candidate)
        # Simulate five batch pre-claims (PostgresInboxSource): lease fencing
        # only, no processing delivery.
        record = await session.scalar(
            select(ExecutionCommandInboxORM).where(
                ExecutionCommandInboxORM.command_id == candidate.command_id
            )
        )
        assert record is not None
        record.claim_generation += 5
        await session.commit()

    for attempt in range(1, 3):
        async with session_factory() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("inbox-budget-test"),
            )
            claim = await PostgresInbox(session, max_claim_attempts=2).claim(
                candidate,
                now=NOW + timedelta(seconds=attempt * 10),
                claim_ttl=timedelta(seconds=1),
            )
            # Both real deliveries fit the budget despite claim_generation
            # already sitting far above the cap.
            assert claim.status == "claimed"
            await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("inbox-budget-test"),
        )
        final = await PostgresInbox(session, max_claim_attempts=2).claim(
            candidate,
            now=NOW + timedelta(minutes=5),
            claim_ttl=timedelta(seconds=30),
        )
        await session.commit()
    assert final.status == "completed"
    assert final.result is not None
    assert final.result.rejection_code == "COMMAND_DEAD_LETTERED"
