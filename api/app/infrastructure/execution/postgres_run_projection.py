"""Queries over formal Run, approval, and resource-build projections."""

import hmac
from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.queries import ResourceBuildView
from app.domain.execution.run import (
    RunState,
    RunStatus,
    validated_run_policy_snapshot,
)
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.infrastructure.execution.models import (
    ExecutionActivityProjectionORM,
    ExecutionApprovalProjectionORM,
    ExecutionResourceBuildProjectionORM,
    ExecutionRunProjectionORM,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresRunProjection:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext | None,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def latest_active_run_id(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
    ) -> UUID | None:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            return await session.scalar(
                select(ExecutionRunProjectionORM.run_id)
                .where(
                    ExecutionRunProjectionORM.source_entity_type == source_entity_type,
                    ExecutionRunProjectionORM.source_entity_id == source_entity_id,
                    ExecutionRunProjectionORM.terminal.is_(False),
                    self._scope_filter(owner_scope),
                )
                .order_by(
                    ExecutionRunProjectionORM.updated_at.desc(),
                    ExecutionRunProjectionORM.run_id.desc(),
                )
                .limit(1)
            )

    async def run_id_for_pending_approval(
        self,
        *,
        approval_id: UUID,
        owner_scope: OwnerScope,
    ) -> UUID | None:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            return await session.scalar(
                select(ExecutionRunProjectionORM.run_id)
                .where(
                    ExecutionRunProjectionORM.terminal.is_(False),
                    ExecutionRunProjectionORM.state["pending_approval_id"].astext
                    == str(approval_id),
                    self._scope_filter(owner_scope),
                )
                .order_by(ExecutionRunProjectionORM.updated_at.desc())
                .limit(1)
            )

    async def status_for_run(
        self,
        *,
        run_id: UUID,
        owner_scope: OwnerScope,
    ) -> RunStatus | None:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            projected = await session.scalar(
                select(ExecutionRunProjectionORM.status).where(
                    ExecutionRunProjectionORM.run_id == run_id,
                    self._scope_filter(owner_scope),
                )
            )
            if projected is not None:
                return RunStatus(projected)
            return None

    async def approval_stats(self, since: datetime) -> dict[str, Any]:
        """Aggregate approval lifecycle data from the formal projection."""
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                await session.execute(
                    select(
                        ExecutionApprovalProjectionORM.status,
                        ExecutionApprovalProjectionORM.requested_at,
                        ExecutionApprovalProjectionORM.decided_at,
                    ).where(ExecutionApprovalProjectionORM.requested_at >= since)
                )
            ).all()
        outcomes = {"approved": 0, "rejected": 0, "cancelled": 0}
        durations: list[float] = []
        pending_count = 0
        for status, requested_at, decided_at in rows:
            if status == "pending":
                pending_count += 1
            elif status in outcomes:
                outcomes[status] += 1
            if decided_at is not None:
                durations.append((decided_at - requested_at).total_seconds())
        return {
            "pending_count": pending_count,
            "outcomes": outcomes,
            "avg_decision_seconds": (sum(durations) / len(durations) if durations else None),
        }

    async def execution_metrics(
        self,
        *,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> dict[str, int]:
        """Count formal Run, tool Activity, and approval facts."""
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            run_stmt = select(func.count()).select_from(ExecutionRunProjectionORM)
            tool_stmt = (
                select(func.count())
                .select_from(ExecutionActivityProjectionORM)
                .where(ExecutionActivityProjectionORM.activity_type == "tool.call")
            )
            approval_stmt = select(func.count()).select_from(ExecutionApprovalProjectionORM)
            failure_stmt = (
                select(func.count())
                .select_from(ExecutionActivityProjectionORM)
                .where(
                    ExecutionActivityProjectionORM.activity_type == "tool.call",
                    ExecutionActivityProjectionORM.status.in_(("failed", "unknown")),
                )
            )
            if start_at is not None:
                run_stmt = run_stmt.where(ExecutionRunProjectionORM.created_at >= start_at)
                tool_stmt = tool_stmt.where(ExecutionActivityProjectionORM.created_at >= start_at)
                approval_stmt = approval_stmt.where(
                    ExecutionApprovalProjectionORM.requested_at >= start_at
                )
                failure_stmt = failure_stmt.where(
                    ExecutionActivityProjectionORM.created_at >= start_at
                )
            if end_at is not None:
                run_stmt = run_stmt.where(ExecutionRunProjectionORM.created_at <= end_at)
                tool_stmt = tool_stmt.where(ExecutionActivityProjectionORM.created_at <= end_at)
                approval_stmt = approval_stmt.where(
                    ExecutionApprovalProjectionORM.requested_at <= end_at
                )
                failure_stmt = failure_stmt.where(
                    ExecutionActivityProjectionORM.created_at <= end_at
                )
            return {
                "run_count": int(await session.scalar(run_stmt) or 0),
                "tool_activity_count": int(await session.scalar(tool_stmt) or 0),
                "approval_request_count": int(await session.scalar(approval_stmt) or 0),
                "tool_activity_failure_count": int(await session.scalar(failure_stmt) or 0),
            }

    async def governance_daily(self, since: datetime) -> list[dict[str, Any]]:
        """Return daily approval requests and failed/unknown tool Activities."""
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            approval_times = (
                await session.scalars(
                    select(ExecutionApprovalProjectionORM.requested_at).where(
                        ExecutionApprovalProjectionORM.requested_at >= since
                    )
                )
            ).all()
            failure_times = (
                await session.scalars(
                    select(ExecutionActivityProjectionORM.terminal_at).where(
                        ExecutionActivityProjectionORM.activity_type == "tool.call",
                        ExecutionActivityProjectionORM.status.in_(("failed", "unknown")),
                        ExecutionActivityProjectionORM.terminal_at >= since,
                    )
                )
            ).all()
        daily: dict[str, dict[str, int]] = defaultdict(
            lambda: {"approval_requests": 0, "activity_failures": 0}
        )
        for occurred_at in approval_times:
            daily[occurred_at.date().isoformat()]["approval_requests"] += 1
        for occurred_at in failure_times:
            if occurred_at is not None:
                daily[occurred_at.date().isoformat()]["activity_failures"] += 1
        return [{"date": date, **counts} for date, counts in sorted(daily.items())]

    async def source_governance(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
    ) -> dict[str, Any]:
        """Read and cryptographically verify formal execution facts."""
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            runs = (
                await session.scalars(
                    select(ExecutionRunProjectionORM)
                    .where(
                        ExecutionRunProjectionORM.source_entity_type == source_entity_type,
                        ExecutionRunProjectionORM.source_entity_id == source_entity_id,
                        self._scope_filter(owner_scope),
                    )
                    .order_by(
                        ExecutionRunProjectionORM.created_at,
                        ExecutionRunProjectionORM.run_id,
                    )
                )
            ).all()
            run_ids = [run.run_id for run in runs]
            approvals = (
                (
                    await session.scalars(
                        select(ExecutionApprovalProjectionORM)
                        .where(ExecutionApprovalProjectionORM.run_id.in_(run_ids))
                        .order_by(
                            ExecutionApprovalProjectionORM.requested_at,
                            ExecutionApprovalProjectionORM.approval_id,
                        )
                    )
                ).all()
                if run_ids
                else []
            )
            activities = (
                (
                    await session.scalars(
                        select(ExecutionActivityProjectionORM)
                        .where(ExecutionActivityProjectionORM.run_id.in_(run_ids))
                        .order_by(
                            ExecutionActivityProjectionORM.created_at,
                            ExecutionActivityProjectionORM.activity_id,
                        )
                    )
                ).all()
                if run_ids
                else []
            )
            checked_entries = 0
            for run in runs:
                state = RunState.model_validate(run.state)
                snapshot = validated_run_policy_snapshot(state)
                if (
                    state.run_id != run.run_id
                    or state.stream_version != run.stream_version
                    or snapshot.execution_revision_id != run.execution_policy_revision_id
                    or snapshot.execution_policy_digest != run.execution_policy_digest
                    or not hmac.compare_digest(
                        canonical_state_hash(state),
                        run.state_hash,
                    )
                    or len(run.last_event_hash) != 64
                ):
                    raise ValueError("formal Run projection integrity mismatch")
                checked_entries += run.stream_version

        return {
            "chain": {
                "verified": True,
                "checked_runs": len(run_ids),
                "checked_entries": checked_entries,
            },
            "runs": [
                {
                    "run_id": str(run.run_id),
                    "family": run.family,
                    "status": run.status,
                    "execution_policy_revision_id": str(run.execution_policy_revision_id),
                    "execution_policy_digest": run.execution_policy_digest,
                    "created_at": run.created_at.isoformat(),
                    "updated_at": run.updated_at.isoformat(),
                    "terminal_at": run.terminal_at.isoformat()
                    if run.terminal_at is not None
                    else None,
                }
                for run in runs
            ],
            "approvals": [
                {
                    "approval_id": str(item.approval_id),
                    "run_id": str(item.run_id),
                    "approval_kind": item.approval_kind,
                    "subject_activity_id": str(item.subject_activity_id),
                    "subject_label": item.subject_label,
                    "risk_summary": item.risk_summary,
                    "status": item.status,
                    "decision": item.decision,
                    "decided_by_user_id": item.decided_by_user_id,
                    "feedback": item.feedback,
                    "requested_at": item.requested_at.isoformat(),
                    "decided_at": item.decided_at.isoformat()
                    if item.decided_at is not None
                    else None,
                }
                for item in approvals
            ],
            "activities": [
                {
                    "activity_id": str(item.activity_id),
                    "run_id": str(item.run_id),
                    "activity_type": item.activity_type,
                    "status": item.status,
                    "attempt": item.attempt,
                    "failure_code": item.failure_code,
                    "created_at": item.created_at.isoformat(),
                    "terminal_at": item.terminal_at.isoformat()
                    if item.terminal_at is not None
                    else None,
                }
                for item in activities
            ],
        }

    async def resource_build(
        self,
        *,
        build_id: str,
        owner_scope: OwnerScope,
    ) -> ResourceBuildView | None:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            record = await session.scalar(
                select(ExecutionResourceBuildProjectionORM).where(
                    ExecutionResourceBuildProjectionORM.build_id == build_id,
                    self._resource_scope_filter(owner_scope),
                )
            )
        return self._resource_build_view(record)

    async def active_resource_build(
        self,
        *,
        resource_kind: str,
        resource_id: str,
        owner_scope: OwnerScope,
    ) -> ResourceBuildView | None:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            record = await session.scalar(
                select(ExecutionResourceBuildProjectionORM)
                .where(
                    ExecutionResourceBuildProjectionORM.resource_kind == resource_kind,
                    ExecutionResourceBuildProjectionORM.resource_id == resource_id,
                    ExecutionResourceBuildProjectionORM.status.not_in(
                        (
                            RunStatus.COMPLETED.value,
                            RunStatus.FAILED.value,
                            RunStatus.CANCELLED.value,
                        )
                    ),
                    self._resource_scope_filter(owner_scope),
                )
                .order_by(
                    ExecutionResourceBuildProjectionORM.updated_at.desc(),
                    ExecutionResourceBuildProjectionORM.run_id.desc(),
                )
                .limit(1)
            )
        return self._resource_build_view(record)

    @staticmethod
    def _resource_build_view(record) -> ResourceBuildView | None:
        if record is None or record.candidate_version_id is None:
            return None
        return ResourceBuildView(
            build_id=record.build_id,
            run_id=record.run_id,
            resource_kind=record.resource_kind,
            resource_id=record.resource_id,
            status=RunStatus(record.status),
            phase=record.phase,
            progress=record.progress,
            active_version_id=record.active_version_id,
            candidate_version_id=record.candidate_version_id,
            failure_code=record.failure_code,
            created_at=record.created_at,
            updated_at=record.updated_at,
            terminal_at=record.terminal_at,
        )

    @staticmethod
    def _scope_filter(owner_scope: OwnerScope):
        if owner_scope.type == OwnerScopeType.PERSONAL:
            return ExecutionRunProjectionORM.owner_user_id == owner_scope.user_id
        if owner_scope.type == OwnerScopeType.TEAM and owner_scope.team_id:
            return ExecutionRunProjectionORM.team_id == owner_scope.team_id
        raise ValueError("team scope requires team_id")

    @staticmethod
    def _resource_scope_filter(owner_scope: OwnerScope):
        if owner_scope.type == OwnerScopeType.PERSONAL:
            return ExecutionResourceBuildProjectionORM.owner_user_id == owner_scope.user_id
        if owner_scope.type == OwnerScopeType.TEAM and owner_scope.team_id:
            return ExecutionResourceBuildProjectionORM.team_id == owner_scope.team_id
        raise ValueError("team scope requires team_id")


__all__ = ["PostgresRunProjection"]
