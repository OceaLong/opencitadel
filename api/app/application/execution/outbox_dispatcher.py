"""Durable outbox orchestration over injected persistence and wake-up ports."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.application.ports.execution import (
    ApprovalNotifierPort,
    ApprovalWaitingNotice,
    OutboxClaim,
    OutboxStorePort,
    WakeupMessage,
    WakeupPublisherPort,
)

logger = logging.getLogger(__name__)

# Outbox destination for durable approval reviewer notices (K4-2). Rows carry
# the notice as their payload; everything else on the stream is a wakeup hint.
APPROVAL_NOTICE_DESTINATION = "approval.notice"


@dataclass(frozen=True)
class DispatchStats:
    claimed: int
    published: int
    failed: int


class OutboxDispatcher:
    def __init__(
        self,
        *,
        store: OutboxStorePort,
        publisher: WakeupPublisherPort,
        approval_notifier: ApprovalNotifierPort | None = None,
        claim_ttl: timedelta = timedelta(seconds=30),
        base_retry_delay: timedelta = timedelta(seconds=1),
        max_retry_delay: timedelta = timedelta(minutes=5),
        # Exception types absorbed per claim (failed + retried with backoff).
        # The persistence adapter contributes its driver exceptions (e.g.
        # SQLAlchemy's) at wiring time — the application layer deliberately
        # does not import persistence libraries.
        delivery_errors: tuple[type[Exception], ...] = (OSError, RuntimeError, ValueError),
    ) -> None:
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        if base_retry_delay <= timedelta(0):
            raise ValueError("base_retry_delay must be positive")
        if max_retry_delay < base_retry_delay:
            raise ValueError("max_retry_delay must not be smaller than base delay")
        self._store = store
        self._publisher = publisher
        self._approval_notifier = approval_notifier
        self._claim_ttl = claim_ttl
        self._base_retry_delay = base_retry_delay
        self._max_retry_delay = max_retry_delay
        self._delivery_errors = delivery_errors

    async def dispatch_batch(self, *, limit: int, now: datetime) -> DispatchStats:
        claims = await self._store.claim_batch(
            limit=limit,
            now=now,
            claim_ttl=self._claim_ttl,
        )
        published = failed = 0
        for claim in claims:
            try:
                await self._deliver(claim)
            except self._delivery_errors as error:
                acknowledged = await self._mark_failed(
                    claim,
                    now=now,
                    error_type=type(error).__name__,
                )
                failed += int(acknowledged)
                continue
            acknowledged = await self._store.mark_delivered(claim, now=now)
            published += int(acknowledged)
            failed += int(not acknowledged)
        return DispatchStats(len(claims), published, failed)

    async def _deliver(self, claim: OutboxClaim) -> None:
        if claim.destination == APPROVAL_NOTICE_DESTINATION:
            # Durable approval notice (K4-2): rebuild the notice from the
            # persisted payload and hand it to the notifier. Failures leave the
            # row undelivered so the next pass redelivers it; the projector's
            # dedupe_key keeps replays from ever inserting a duplicate row.
            if self._approval_notifier is None:
                raise RuntimeError("no approval notifier configured for approval.notice outbox row")
            await self._approval_notifier.approval_waiting(self._decode_notice(claim))
            return
        await self._publisher.publish(
            WakeupMessage(
                destination=claim.destination,
                dedupe_key=claim.dedupe_key,
                event_position=claim.event_position,
            )
        )

    @staticmethod
    def _decode_notice(claim: OutboxClaim) -> ApprovalWaitingNotice:
        payload = claim.payload
        if not isinstance(payload, dict):
            # ValueError on purpose (not TypeError): this is a bad persisted
            # row, absorbed by the delivery-error boundary and retried.
            raise ValueError("approval.notice outbox row is missing its payload")  # noqa: TRY004
        try:
            return ApprovalWaitingNotice(
                user_id=payload.get("user_id") or None,
                approval_id=UUID(str(payload["approval_id"])),
                run_id=UUID(str(payload["run_id"])),
                session_id=payload.get("session_id") or None,
                subject_label=str(payload.get("subject_label") or ""),
                team_id=payload.get("team_id") or None,
                kind=str(payload.get("kind") or "approval_waiting"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid approval.notice payload: {error}") from error

    async def _mark_failed(
        self,
        claim: OutboxClaim,
        *,
        now: datetime,
        error_type: str,
    ) -> bool:
        return await self._store.mark_failed(
            claim,
            now=now,
            error_type=error_type,
            base_retry_delay=self._base_retry_delay,
            max_retry_delay=self._max_retry_delay,
        )


__all__ = ["APPROVAL_NOTICE_DESTINATION", "DispatchStats", "OutboxDispatcher"]
