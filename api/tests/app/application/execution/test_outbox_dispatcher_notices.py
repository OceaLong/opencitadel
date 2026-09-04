"""Approval notices flow through the durable outbox (K4-2).

The dispatcher routes ``approval.notice`` rows to the approval notifier
(rebuilding the notice from the persisted payload) and everything else to the
wakeup publisher; a notifier failure leaves the row undelivered for redelivery.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.execution.outbox_dispatcher import (
    APPROVAL_NOTICE_DESTINATION,
    OutboxDispatcher,
)
from app.application.ports.execution import ApprovalWaitingNotice, OutboxClaim

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _notice_claim(payload: dict | None) -> OutboxClaim:
    return OutboxClaim(
        outbox_id=uuid4(),
        event_position=7,
        destination=APPROVAL_NOTICE_DESTINATION,
        dedupe_key=f"{APPROVAL_NOTICE_DESTINATION}:{uuid4()}",
        generation=1,
        attempt=1,
        payload=payload,
    )


def _payload(approval_id, run_id) -> dict:
    return {
        "user_id": "u1",
        "approval_id": str(approval_id),
        "run_id": str(run_id),
        "session_id": "session-1",
        "subject_label": "write_external",
        "team_id": None,
        "kind": "approval_waiting",
    }


class _Store:
    def __init__(self, claims: tuple[OutboxClaim, ...]) -> None:
        self._claims = claims
        self.delivered: list[OutboxClaim] = []
        self.failed: list[tuple[OutboxClaim, str]] = []

    async def claim_batch(self, *, limit, now, claim_ttl):
        del limit, now, claim_ttl
        result, self._claims = self._claims, ()
        return result

    async def mark_delivered(self, claim, *, now):
        del now
        self.delivered.append(claim)
        return True

    async def mark_failed(self, claim, *, now, error_type, base_retry_delay, max_retry_delay):
        del now, base_retry_delay, max_retry_delay
        self.failed.append((claim, error_type))
        return True


class _Publisher:
    def __init__(self) -> None:
        self.messages: list = []

    async def publish(self, message) -> None:
        self.messages.append(message)


class _Notifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.notices: list[ApprovalWaitingNotice] = []
        self.fail = fail

    async def approval_waiting(self, notice: ApprovalWaitingNotice) -> None:
        if self.fail:
            raise RuntimeError("notification store down")
        self.notices.append(notice)


@pytest.mark.asyncio
async def test_notice_claim_is_rebuilt_and_routed_to_the_notifier() -> None:
    approval_id, run_id = uuid4(), uuid4()
    store = _Store((_notice_claim(_payload(approval_id, run_id)),))
    publisher = _Publisher()
    notifier = _Notifier()
    dispatcher = OutboxDispatcher(store=store, publisher=publisher, approval_notifier=notifier)

    stats = await dispatcher.dispatch_batch(limit=10, now=NOW)

    assert stats.published == 1
    assert publisher.messages == []
    assert len(notifier.notices) == 1
    notice = notifier.notices[0]
    assert notice.approval_id == approval_id
    assert notice.run_id == run_id
    assert notice.user_id == "u1"
    assert notice.session_id == "session-1"
    assert notice.kind == "approval_waiting"
    assert len(store.delivered) == 1


@pytest.mark.asyncio
async def test_notifier_crash_marks_the_row_failed_for_redelivery() -> None:
    claim = _notice_claim(_payload(uuid4(), uuid4()))
    store = _Store((claim,))
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=_Publisher(),
        approval_notifier=_Notifier(fail=True),
    )

    stats = await dispatcher.dispatch_batch(limit=10, now=NOW)

    assert stats.failed == 1
    assert store.delivered == []
    assert [entry[1] for entry in store.failed] == ["RuntimeError"]


@pytest.mark.asyncio
async def test_malformed_notice_payload_is_failed_not_crashed() -> None:
    store = _Store(
        (
            _notice_claim(None),
            _notice_claim({"approval_id": "not-a-uuid", "run_id": "nope"}),
        )
    )
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=_Publisher(),
        approval_notifier=_Notifier(),
    )

    stats = await dispatcher.dispatch_batch(limit=10, now=NOW)

    assert stats.failed == 2
    assert {entry[1] for entry in store.failed} == {"ValueError"}


@pytest.mark.asyncio
async def test_wakeup_claims_still_go_to_the_publisher() -> None:
    claim = OutboxClaim(
        outbox_id=uuid4(),
        event_position=3,
        destination="execution.events",
        dedupe_key=f"event:{uuid4()}",
        generation=1,
        attempt=1,
    )
    store = _Store((claim,))
    publisher = _Publisher()
    notifier = _Notifier()
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=publisher,
        approval_notifier=notifier,
        claim_ttl=timedelta(seconds=30),
    )

    stats = await dispatcher.dispatch_batch(limit=10, now=NOW)

    assert stats.published == 1
    assert len(publisher.messages) == 1
    assert publisher.messages[0].destination == "execution.events"
    assert notifier.notices == []
