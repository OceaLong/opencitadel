"""PostgreSQL Effect claims with SKIP LOCKED leases and generation fencing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.kernel.application.effect_worker import EffectClaim, ExpiredEffect
from app.kernel.application.timer_worker import TimerClaim
from app.kernel.domain.types import EffectSafety, OwnerScopeRef, Workflow

from .models import KernelEffectORM, KernelRunORM, KernelTimerORM
from .session_auth import bind_context


async def _bind_worker(session: AsyncSession) -> None:
    from app.domain.models.authorization import AuthorizationContext

    await bind_context(session, AuthorizationContext.system("kernel-worker"))


class PostgresEffectClaimStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        decrypt_request: Callable[[str], dict[str, Any]],
    ) -> None:
        self._session_factory = session_factory
        self._decrypt_request = decrypt_request

    async def recover_expired(self, *, now: datetime) -> tuple[ExpiredEffect, ...]:
        unknown: list[ExpiredEffect] = []
        async with self._session_factory() as session, session.begin():
            await _bind_worker(session)
            rows = (
                await session.scalars(
                    select(KernelEffectORM)
                    .where(
                        KernelEffectORM.status.in_(("claimed", "started")),
                        KernelEffectORM.lease_expires_at < now,
                    )
                    .order_by(KernelEffectORM.updated_at, KernelEffectORM.id)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                claim = await self._claim_value(session, row)
                if (
                    row.status == "started"
                    and row.safety == EffectSafety.NON_IDEMPOTENT_WRITE.value
                ):
                    row.status = "unknown"
                    row.error_code = "effect_outcome_unknown"
                    row.error_message = "Effect lease expired after external work started"
                    row.updated_at = now
                    unknown.append(ExpiredEffect(claim=claim, resolution="unknown"))
                elif row.attempt_count >= row.max_attempts:
                    row.status = "failed"
                    row.error_code = "effect_attempts_exhausted"
                    row.error_message = "Effect retry budget exhausted"
                    row.updated_at = now
                else:
                    row.status = "ready"
                    row.claim_owner = None
                    row.lease_expires_at = None
                    row.heartbeat_at = None
                    row.started_at = None
                    row.next_attempt_at = now
                    row.updated_at = now
        return tuple(unknown)

    async def claim_ready(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[EffectClaim, ...]:
        if limit < 1 or lease_seconds < 1:
            raise ValueError("claim bounds must be positive")
        claims: list[EffectClaim] = []
        async with self._session_factory() as session, session.begin():
            await _bind_worker(session)
            rows = (
                await session.scalars(
                    select(KernelEffectORM)
                    .where(
                        KernelEffectORM.status == "ready",
                        KernelEffectORM.next_attempt_at <= now,
                    )
                    .order_by(KernelEffectORM.next_attempt_at, KernelEffectORM.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                row.status = "claimed"
                row.claim_owner = worker_id
                row.claim_generation += 1
                row.attempt_count += 1
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                row.heartbeat_at = now
                row.updated_at = now
                claims.append(await self._claim_value(session, row))
        return tuple(claims)

    async def mark_started(
        self,
        effect_id: UUID,
        claim_generation: int,
        *,
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            await _bind_worker(session)
            result = await session.execute(
                update(KernelEffectORM)
                .where(
                    KernelEffectORM.id == effect_id,
                    KernelEffectORM.claim_generation == claim_generation,
                    KernelEffectORM.status == "claimed",
                    KernelEffectORM.lease_expires_at >= now,
                )
                .values(status="started", started_at=now, heartbeat_at=now, updated_at=now)
            )
            return result.rowcount == 1

    async def mark_retry(
        self,
        effect_id: UUID,
        claim_generation: int,
        *,
        now: datetime,
        code: str,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            await _bind_worker(session)
            row = await session.scalar(
                select(KernelEffectORM)
                .where(
                    KernelEffectORM.id == effect_id,
                    KernelEffectORM.claim_generation == claim_generation,
                    KernelEffectORM.status == "started",
                )
                .with_for_update()
            )
            if row is None:
                return False
            delay_seconds = min(300, 2 ** min(row.attempt_count, 8))
            row.status = "ready"
            row.claim_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.started_at = None
            row.next_attempt_at = now + timedelta(seconds=delay_seconds)
            row.error_code = code
            row.error_message = "Effect attempt scheduled for retry"
            row.updated_at = now
            return True

    async def _claim_value(
        self,
        session: AsyncSession,
        row: KernelEffectORM,
    ) -> EffectClaim:
        run = await session.get(KernelRunORM, row.run_id)
        if run is None:
            raise RuntimeError("Effect references a missing Run")
        return EffectClaim(
            effect_id=row.id,
            invocation_id=row.invocation_id,
            run_id=row.run_id,
            workflow=Workflow(run.workflow),
            effect_type=row.effect_type,
            safety=EffectSafety(row.safety),
            request=self._decrypt_request(row.request_ciphertext),
            owner_scope=OwnerScopeRef(
                owner_user_id=row.owner_user_id,
                team_id=row.team_id,
            ),
            claim_generation=row.claim_generation,
            timeout_seconds=row.timeout_seconds,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
        )


class PostgresTimerClaimStore:
    """Lease due timers; the timer UUID is also the idempotent command UUID."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[TimerClaim, ...]:
        if limit < 1 or lease_seconds < 1:
            raise ValueError("timer claim bounds must be positive")
        claims: list[TimerClaim] = []
        async with self._session_factory() as session, session.begin():
            await _bind_worker(session)
            await session.execute(
                update(KernelTimerORM)
                .where(
                    KernelTimerORM.status == "claimed",
                    KernelTimerORM.lease_expires_at < now,
                )
                .values(status="pending", claim_owner=None, lease_expires_at=None)
            )
            rows = (
                await session.scalars(
                    select(KernelTimerORM)
                    .where(
                        KernelTimerORM.status == "pending",
                        KernelTimerORM.due_at <= now,
                    )
                    .order_by(KernelTimerORM.due_at, KernelTimerORM.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                row.status = "claimed"
                row.claim_owner = worker_id
                row.claim_generation += 1
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                run = await session.get(KernelRunORM, row.run_id)
                if run is None:
                    raise RuntimeError("Timer references a missing Run")
                claims.append(
                    TimerClaim(
                        timer_id=row.id,
                        run_id=row.run_id,
                        workflow=Workflow(run.workflow),
                        command_type=row.command_type,
                        command_payload=row.command_payload,
                        owner_scope=OwnerScopeRef(
                            owner_user_id=row.owner_user_id,
                            team_id=row.team_id,
                        ),
                        claim_generation=row.claim_generation,
                    )
                )
        return tuple(claims)

    async def mark_fired(
        self,
        timer_id: UUID,
        claim_generation: int,
        *,
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            await _bind_worker(session)
            result = await session.execute(
                update(KernelTimerORM)
                .where(
                    KernelTimerORM.id == timer_id,
                    KernelTimerORM.claim_generation == claim_generation,
                    KernelTimerORM.status == "claimed",
                    KernelTimerORM.lease_expires_at >= now,
                )
                .values(
                    status="fired",
                    fired_at=now,
                    claim_owner=None,
                    lease_expires_at=None,
                )
            )
            return result.rowcount == 1
