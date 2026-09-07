from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.models.notification import NotificationDelivery
from app.domain.utils.time_utils import utc_now
from app.infrastructure.repositories.db_notification_repository import DBNotificationRepository


def statement(db):
    return db.execute.await_args.args[0].compile(dialect=postgresql.dialect())


@pytest.mark.asyncio
async def test_claim_uses_skip_locked_and_recovers_expired_leases():
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))),
        flush=AsyncMock(),
    )
    repo = DBNotificationRepository(db)
    assert await repo.claim_deliveries(now=utc_now(), limit=1, lease_seconds=90) == []
    sql = str(statement(db))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "notification_deliveries.lease_until <=" in sql
    assert "notification_deliveries.next_attempt_at <=" in sql


@pytest.mark.asyncio
async def test_retry_is_owner_scoped_and_cannot_resend_successful_delivery():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)))
    assert not await DBNotificationRepository(db).retry_delivery("d", "owner", now=utc_now())
    compiled = statement(db)
    assert "notification_deliveries.user_id =" in str(compiled)
    assert "owner" in compiled.params.values()
    assert ["failed", "retrying"] in compiled.params.values()


@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_new_claim():
    db = SimpleNamespace(execute=AsyncMock())
    row = NotificationDelivery(
        id="d", user_id="u", scope={}, channel={}, message="m", status="sent", attempts=2
    )
    await DBNotificationRepository(db).finish_delivery(row)
    compiled = statement(db)
    assert "notification_deliveries.attempts =" in str(compiled).split("WHERE")[1]
    assert "notification_deliveries.status =" in str(compiled).split("WHERE")[1]


@pytest.mark.asyncio
async def test_replayed_business_notification_is_idempotent():
    from app.domain.models.notification import Notification

    db = SimpleNamespace(execute=AsyncMock(), add=lambda _: None)
    await DBNotificationRepository(db).save(
        Notification(id="event-1", user_id="u", type="job_started")
    )
    assert "ON CONFLICT (id) DO NOTHING" in str(statement(db))


@pytest.mark.asyncio
async def test_manual_retry_preserves_claim_generation_to_prevent_stale_worker_aba():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=1)))
    await DBNotificationRepository(db).retry_delivery("d", "owner", now=utc_now())
    compiled = statement(db)
    updates = str(compiled).split("SET", 1)[1].split("WHERE", 1)[0]
    assert ", attempts=" not in updates
    assert "max_attempts=(notification_deliveries.attempts +" in updates
