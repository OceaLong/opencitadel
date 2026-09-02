"""Owner-scoped query and server-authored disposition adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.kernel.domain.types import OwnerScopeRef

from .models import (
    KernelApprovalReviewerORM,
    KernelApprovalViewORM,
    KernelEffectViewORM,
    KernelMessageViewORM,
    KernelPublicEventORM,
    KernelRunViewORM,
)
from .session_auth import bind_context


def _scope_filter(model, scope: OwnerScopeRef):
    if scope.team_id is not None:
        return model.team_id == scope.team_id
    return (model.team_id.is_(None)) & (model.owner_user_id == scope.owner_user_id)


class PostgresKernelQueryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_runs(
        self,
        scope: OwnerScopeRef,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        statement = select(KernelRunViewORM).where(_scope_filter(KernelRunViewORM, scope))
        if status is not None:
            statement = statement.where(KernelRunViewORM.status == status)
        statement = statement.order_by(KernelRunViewORM.updated_at.desc()).limit(limit)
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (await session.scalars(statement)).all()
        return [self._run(row) for row in rows]

    async def get_run(
        self,
        run_id: UUID,
        scope: OwnerScopeRef,
    ) -> dict[str, object] | None:
        async with self._session_factory() as session:
            await bind_context(session)
            row = await session.scalar(
                select(KernelRunViewORM).where(
                    KernelRunViewORM.id == run_id,
                    _scope_filter(KernelRunViewORM, scope),
                )
            )
            if row is None:
                return None
            messages = (
                await session.scalars(
                    select(KernelMessageViewORM)
                    .where(KernelMessageViewORM.run_id == run_id)
                    .order_by(KernelMessageViewORM.event_version)
                )
            ).all()
            effects = (
                await session.scalars(
                    select(KernelEffectViewORM)
                    .where(KernelEffectViewORM.run_id == run_id)
                    .order_by(KernelEffectViewORM.created_at)
                )
            ).all()
        return {
            **self._run(row),
            "messages": [
                {
                    "id": str(value.id),
                    "role": value.role,
                    "content": value.content,
                    "eventVersion": value.event_version,
                    "createdAt": value.created_at.isoformat(),
                }
                for value in messages
            ],
            "effects": [
                {
                    "id": str(value.id),
                    "type": value.effect_type,
                    "status": value.status,
                    "summary": value.public_summary,
                    "approvalId": str(value.approval_id) if value.approval_id else None,
                }
                for value in effects
            ],
        }

    async def history(
        self,
        run_id: UUID,
        scope: OwnerScopeRef,
        *,
        after_version: int = 0,
    ) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            await bind_context(session)
            visible = await session.scalar(
                select(KernelRunViewORM.id).where(
                    KernelRunViewORM.id == run_id,
                    _scope_filter(KernelRunViewORM, scope),
                )
            )
            if visible is None:
                return []
            rows = (
                await session.scalars(
                    select(KernelPublicEventORM)
                    .where(
                        KernelPublicEventORM.run_id == run_id,
                        KernelPublicEventORM.event_version > after_version,
                    )
                    .order_by(KernelPublicEventORM.event_version)
                )
            ).all()
        return [
            {
                "id": str(row.event_id),
                "version": row.event_version,
                "type": row.event_type,
                "payload": row.payload,
                "occurredAt": row.occurred_at.isoformat(),
            }
            for row in rows
        ]

    async def list_approvals(
        self,
        actor_user_id: str,
        *,
        status: str | None = None,
        team_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        statement = (
            select(KernelApprovalViewORM)
            .join(
                KernelApprovalReviewerORM,
                KernelApprovalReviewerORM.approval_id == KernelApprovalViewORM.id,
            )
            .where(KernelApprovalReviewerORM.user_id == actor_user_id)
        )
        if status is not None:
            statement = statement.where(KernelApprovalViewORM.status == status)
        if team_id is not None:
            statement = statement.where(KernelApprovalViewORM.team_id == team_id)
        statement = statement.order_by(KernelApprovalViewORM.requested_at.desc()).limit(limit)
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (await session.scalars(statement)).all()
        return [
            {
                "id": str(row.id),
                "runId": str(row.run_id),
                "effectId": str(row.effect_id),
                "subject": row.subject,
                "riskSummary": row.risk_summary,
                "status": row.status,
                "decision": row.decision,
                "feedback": row.feedback,
                "decidedByUserId": row.decided_by_user_id,
                "requestedAt": row.requested_at.isoformat(),
                "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
                "decidedAt": row.decided_at.isoformat() if row.decided_at else None,
                "teamId": row.team_id,
            }
            for row in rows
        ]

    async def approval_context(
        self,
        approval_id: UUID,
        actor_user_id: str,
    ) -> dict[str, object] | None:
        async with self._session_factory() as session:
            await bind_context(session)
            row = await session.scalar(
                select(KernelApprovalViewORM)
                .join(
                    KernelApprovalReviewerORM,
                    KernelApprovalReviewerORM.approval_id == KernelApprovalViewORM.id,
                )
                .where(
                    KernelApprovalViewORM.id == approval_id,
                    KernelApprovalReviewerORM.user_id == actor_user_id,
                )
            )
            if row is None:
                return None
            run = await session.get(KernelRunViewORM, row.run_id)
        if run is None:
            return None
        return {
            "run_id": row.run_id,
            "workflow": run.workflow,
            "owner_user_id": row.owner_user_id,
            "team_id": row.team_id,
        }

    @staticmethod
    def _run(row: KernelRunViewORM) -> dict[str, object]:
        return {
            "id": str(row.id),
            "workflow": row.workflow,
            "title": row.title,
            "status": row.status,
            "currentTurn": row.current_turn,
            "waitReason": row.wait_reason,
            "streamVersion": row.stream_version,
            "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(),
            "archivedAt": row.deleted_at.isoformat() if row.deleted_at else None,
            "purgeAfter": row.purge_after.isoformat() if row.purge_after else None,
            "teamId": row.team_id,
        }


class PostgresDispositionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        retention_days: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._retention_days = retention_days
        self._now = now

    async def preview_run(
        self,
        run_id: UUID,
        scope: OwnerScopeRef,
        *,
        action: str,
    ) -> dict[str, object]:
        if action not in {"archive", "purge"}:
            raise ValueError("unsupported disposition action")
        async with self._session_factory() as session:
            await bind_context(session)
            run = await session.scalar(
                select(KernelRunViewORM).where(
                    KernelRunViewORM.id == run_id,
                    _scope_filter(KernelRunViewORM, scope),
                )
            )
            if run is None:
                raise LookupError("Run not found")
            message_count = await session.scalar(
                select(func.count())
                .select_from(KernelMessageViewORM)
                .where(KernelMessageViewORM.run_id == run_id)
            )
            effect_count = await session.scalar(
                select(func.count())
                .select_from(KernelEffectViewORM)
                .where(KernelEffectViewORM.run_id == run_id)
            )
        now = self._now()
        purge_after = now + timedelta(days=self._retention_days) if action == "archive" else now
        bound: dict[str, object] = {
            "action": action,
            "recoverable": action == "archive",
            "affectedCounts": {
                "messages": int(message_count or 0),
                "effects": int(effect_count or 0),
            },
            "confirmation": f"{action.upper()} RUN {run_id}",
            "runVersion": run.stream_version,
        }
        canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
        return {
            **bound,
            "purgeAfter": purge_after.isoformat(),
            "planHash": hashlib.sha256(canonical.encode()).hexdigest(),
        }

    async def validate_run(
        self,
        run_id: UUID,
        scope: OwnerScopeRef,
        *,
        action: str,
        plan_hash: str,
        confirmation: str,
    ) -> dict[str, object] | None:
        # The digest binds action, current stream head and affected counts. The
        # displayed deadline is intentionally excluded so a sub-second clock
        # change cannot invalidate an otherwise unchanged server plan.
        plan = await self.preview_run(run_id, scope, action=action)
        if not hmac.compare_digest(str(plan["confirmation"]), confirmation):
            return None
        if not hmac.compare_digest(str(plan["planHash"]), plan_hash):
            return None
        return plan
