"""Admission and invocation quota gates over the authoritative PostgreSQL rows."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contexts.identity.models import (
    GovernancePolicyHeadORM,
    GovernancePolicyRevisionORM,
    TeamQuotaORM,
    UserQuotaORM,
)
from app.contexts.knowledge.models import FileORM
from app.domain.errors import TooManyRequestsError
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy.governance import GovernancePolicy, QuotaLimits
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.types import OwnerScopeRef
from app.kernel.infrastructure.postgres.models import KernelRunORM, KernelRunViewORM
from app.kernel.infrastructure.postgres.session_auth import bind_context

from .models import InferenceUsageORM


class PostgresQuotaGate:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def validate_command(
        self,
        session: AsyncSession,
        command: CommandEnvelope,
    ) -> None:
        if command.type != "StartAgent":
            return
        await bind_context(session, AuthorizationContext.system("quota-gate"))
        await self._lock_dimensions(
            session,
            user_id=command.actor_user_id,
            team_id=command.owner_scope.team_id,
        )
        await self._assert_run_allowed(
            session,
            command.owner_scope,
            actor_user_id=command.actor_user_id,
        )

    async def _assert_run_allowed(
        self,
        session: AsyncSession,
        scope: OwnerScopeRef,
        *,
        actor_user_id: str,
    ) -> None:
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        user_limits, team_limits = await self._limits(session, scope, actor_user_id=actor_user_id)
        user_daily = await session.scalar(
            select(func.count())
            .select_from(KernelRunORM)
            .where(
                KernelRunORM.created_by_user_id == actor_user_id,
                KernelRunORM.created_at >= day_start,
            )
        )
        user_concurrent = await session.scalar(
            select(func.count())
            .select_from(KernelRunViewORM)
            .join(KernelRunORM, KernelRunORM.id == KernelRunViewORM.id)
            .where(
                KernelRunORM.created_by_user_id == actor_user_id,
                KernelRunViewORM.status.in_(("running", "waiting")),
            )
        )
        self._check_count(user_daily, user_limits.daily_new_runs, "dailyNewRuns")
        self._check_count(user_concurrent, user_limits.concurrent_runs, "concurrentRuns")
        if scope.team_id and team_limits is not None:
            team_daily = await session.scalar(
                select(func.count())
                .select_from(KernelRunORM)
                .where(
                    KernelRunORM.team_id == scope.team_id,
                    KernelRunORM.created_at >= day_start,
                )
            )
            team_concurrent = await session.scalar(
                select(func.count())
                .select_from(KernelRunViewORM)
                .where(
                    KernelRunViewORM.team_id == scope.team_id,
                    KernelRunViewORM.status.in_(("running", "waiting")),
                )
            )
            self._check_count(team_daily, team_limits.daily_new_runs, "team.dailyNewRuns")
            self._check_count(
                team_concurrent,
                team_limits.concurrent_runs,
                "team.concurrentRuns",
            )

    async def assert_model_allowed(
        self,
        scope: OwnerScopeRef,
        request: dict[str, object],
    ) -> None:
        run_id = UUID(str(request["_run_id"]))
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        async with self._system_session() as session:
            run = await session.get(KernelRunORM, run_id)
            if run is None:
                raise RuntimeError("quota check references a missing Run")
            user_limits, team_limits = await self._limits(
                session,
                scope,
                actor_user_id=run.created_by_user_id,
            )
            user_tokens = await session.scalar(
                select(
                    func.coalesce(
                        func.sum(InferenceUsageORM.input_tokens + InferenceUsageORM.output_tokens),
                        0,
                    )
                ).where(
                    InferenceUsageORM.actor_user_id == run.created_by_user_id,
                    InferenceUsageORM.created_at >= month_start,
                )
            )
            self._check_count(
                user_tokens,
                user_limits.monthly_model_tokens,
                "monthlyModelTokens",
            )
            if scope.team_id and team_limits is not None:
                team_tokens = await session.scalar(
                    select(
                        func.coalesce(
                            func.sum(
                                InferenceUsageORM.input_tokens + InferenceUsageORM.output_tokens
                            ),
                            0,
                        )
                    ).where(
                        InferenceUsageORM.team_id == scope.team_id,
                        InferenceUsageORM.created_at >= month_start,
                    )
                )
                self._check_count(
                    team_tokens,
                    team_limits.monthly_model_tokens,
                    "team.monthlyModelTokens",
                )

    async def validate_storage(
        self,
        session: AsyncSession,
        scope: OwnerScopeRef,
        *,
        actor_user_id: str,
        incoming_bytes: int,
    ) -> None:
        await bind_context(session, AuthorizationContext.system("quota-gate"))
        await self._lock_dimensions(
            session,
            user_id=actor_user_id,
            team_id=scope.team_id,
        )
        user_limits, team_limits = await self._limits(
            session,
            scope,
            actor_user_id=actor_user_id,
        )
        user_bytes = await session.scalar(
            select(func.coalesce(func.sum(FileORM.size), 0)).where(
                FileORM.created_by_user_id == actor_user_id
            )
        )
        self._check_size(
            user_bytes,
            incoming_bytes,
            user_limits.storage_bytes,
            "storageBytes",
        )
        if scope.team_id and team_limits is not None:
            team_bytes = await session.scalar(
                select(func.coalesce(func.sum(FileORM.size), 0)).where(
                    FileORM.team_id == scope.team_id
                )
            )
            self._check_size(
                team_bytes,
                incoming_bytes,
                team_limits.storage_bytes,
                "team.storageBytes",
            )

    @staticmethod
    async def _lock_dimensions(
        session: AsyncSession,
        *,
        user_id: str,
        team_id: str | None,
    ) -> None:
        dimensions = [f"quota:user:{user_id}"]
        if team_id:
            dimensions.append(f"quota:team:{team_id}")
        for dimension in sorted(dimensions):
            digest = hashlib.sha256(dimension.encode()).digest()
            lock_key = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
            await session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def _limits(
        self,
        session: AsyncSession,
        scope: OwnerScopeRef,
        *,
        actor_user_id: str,
    ) -> tuple[QuotaLimits, QuotaLimits | None]:
        head = await session.get(GovernancePolicyHeadORM, 1)
        revision = (
            await session.get(GovernancePolicyRevisionORM, head.revision_id)
            if head is not None
            else None
        )
        if revision is None:
            raise RuntimeError("governance policy is missing")
        policy = GovernancePolicy.model_validate(revision.policy)
        user = await session.get(UserQuotaORM, actor_user_id)
        user_limits = (
            QuotaLimits.model_validate(user, from_attributes=True)
            if user is not None
            else policy.user_quota_defaults
        )
        if not scope.team_id:
            return user_limits, None
        team = await session.get(TeamQuotaORM, scope.team_id)
        team_limits = (
            QuotaLimits.model_validate(team, from_attributes=True)
            if team is not None
            else policy.team_quota_defaults
        )
        return user_limits, team_limits

    @staticmethod
    def _check_count(value: int | None, limit: int | None, dimension: str) -> None:
        if limit is not None and int(value or 0) >= limit:
            raise TooManyRequestsError(
                f"quota exceeded: {dimension}",
                error_key="errors.quotaExceeded",
                error_params={"dimension": dimension},
            )

    @staticmethod
    def _check_size(
        value: int | None,
        incoming: int,
        limit: int | None,
        dimension: str,
    ) -> None:
        if limit is not None and int(value or 0) + incoming > limit:
            raise TooManyRequestsError(
                f"quota exceeded: {dimension}",
                error_key="errors.quotaExceeded",
                error_params={"dimension": dimension},
            )

    def _system_session(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def open_session():
            async with self._session_factory() as session:
                await bind_context(session, AuthorizationContext.system("quota-gate"))
                yield session

        return open_session()


__all__ = ["PostgresQuotaGate"]
