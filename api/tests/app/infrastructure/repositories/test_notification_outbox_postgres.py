"""Real PostgreSQL claims, lease recovery and monotonic delivery fencing."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from app.domain.models.notification import NotificationDelivery
from app.infrastructure.models.notification import NotificationDeliveryModel
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_notification_repository import DBNotificationRepository
from tests.app.execution_test_support import execution_admin_session

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("postgres_integration")]


async def test_concurrent_claim_expiry_and_stale_completion_are_fenced():
    owner = str(uuid4())
    now = datetime(2001, 1, 1, tzinfo=UTC)
    ids = [str(uuid4()), str(uuid4())]
    try:
        async with execution_admin_session() as session:
            session.add(UserORM(id=owner, email=f"{owner}@example.com", username=owner))
            await session.flush()
            repo = DBNotificationRepository(session)
            for delivery_id in ids:
                delivery = NotificationDelivery(
                    id=delivery_id,
                    user_id=owner,
                    scope={"user_id": owner},
                    channel={"type": "email"},
                    message="test",
                    next_attempt_at=now,
                )
                await repo.enqueue_delivery(delivery)
                await repo.enqueue_delivery(delivery)
            await session.commit()
        async with execution_admin_session() as first, execution_admin_session() as second:
            claim1 = await DBNotificationRepository(first).claim_deliveries(
                now=now + timedelta(seconds=1), limit=1, lease_seconds=90
            )
            claim2 = await asyncio.wait_for(
                DBNotificationRepository(second).claim_deliveries(
                    now=now + timedelta(seconds=1), limit=1, lease_seconds=90
                ),
                timeout=2,
            )
            assert len(claim1) == len(claim2) == 1
            assert claim1[0].id != claim2[0].id
            assert {claim1[0].id, claim2[0].id} == set(ids)
            await first.commit()
            await second.commit()
        old = claim1[0]
        async with execution_admin_session() as session:
            await session.execute(
                update(NotificationDeliveryModel)
                .where(NotificationDeliveryModel.id == old.id)
                .values(lease_until=now - timedelta(seconds=1))
            )
            await session.commit()
        async with execution_admin_session() as session:
            current = (
                await DBNotificationRepository(session).claim_deliveries(
                    now=now, limit=1, lease_seconds=90
                )
            )[0]
            assert current.id == old.id
            assert current.attempts == old.attempts + 1
            await session.commit()
        old.status = "sent"
        async with execution_admin_session() as session:
            await DBNotificationRepository(session).finish_delivery(old)
            await session.commit()
        async with execution_admin_session() as session:
            row = await DBNotificationRepository(session).get_delivery(old.id, owner)
            assert row.status == "sending"
            assert row.attempts == current.attempts
            assert await DBNotificationRepository(session).get_delivery(old.id, "not-owner") is None
        current.status = "failed"
        async with execution_admin_session() as session:
            await DBNotificationRepository(session).finish_delivery(current)
            await session.commit()
        async with execution_admin_session() as session:
            repo = DBNotificationRepository(session)
            assert not await repo.retry_delivery(current.id, "not-owner", now=now)
            assert await repo.retry_delivery(current.id, owner, now=now)
            await session.commit()
        async with execution_admin_session() as session:
            next_claim = (
                await DBNotificationRepository(session).claim_deliveries(
                    now=now, limit=1, lease_seconds=90
                )
            )[0]
            assert next_claim.attempts == current.attempts + 1
            assert next_claim.max_attempts == current.attempts + 5
            await session.commit()
        async with execution_admin_session() as session:
            count = len(
                (
                    await session.scalars(
                        select(NotificationDeliveryModel).where(
                            NotificationDeliveryModel.user_id == owner
                        )
                    )
                ).all()
            )
            assert count == 2
    finally:
        async with execution_admin_session() as session:
            await session.execute(delete(UserORM).where(UserORM.id == owner))
            await session.commit()
