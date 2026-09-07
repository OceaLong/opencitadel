"""Kernel-owned projection recovery driven by durable administrator requests."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from app.application.execution.projection_recovery import rebuild_verified_scope
from app.domain.models.audit_log import AuditLog
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.models import (
    ExecutionPoisonedRunORM,
    ExecutionPoisonedScopeORM,
    ExecutionRecoveryRequestORM,
)
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.postgres_run_decision_source import PostgresRunDecisionSource
from app.infrastructure.repositories.db_audit_repository import DBAuditRepository
from app.infrastructure.security.db_authorization import configure_session_authorization


def scope_key(scope: OwnerScope) -> str:
    return f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"


class PostgresRecoveryWorker:
    def __init__(
        self, *, session_factory, authorization, audit_signing_key: str, audit_signing_key_id: str
    ):
        self._sessions = session_factory
        self._authorization = authorization
        self._audit_key = audit_signing_key
        self._audit_key_id = audit_signing_key_id
        self._projector = PostgresFormalProjector(
            session_factory=session_factory, authorization=authorization
        )
        self._source = PostgresRunDecisionSource(
            session_factory=session_factory, authorization=authorization
        )

    async def mark(self, scope: OwnerScope) -> None:
        now = datetime.now(UTC)
        async with self._sessions() as session:
            await configure_session_authorization(session, self._authorization)
            await session.execute(
                pg_insert(ExecutionPoisonedScopeORM)
                .values(
                    owner_scope_key=scope_key(scope),
                    owner_user_id=None if scope.team_id else scope.user_id,
                    team_id=scope.team_id,
                    reason="rebuilding",
                    last_error="Administrator requested recovery",
                    rebuilding=True,
                    failure_count=0,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["owner_scope_key"],
                    set_={"rebuilding": True, "last_seen_at": now},
                )
            )
            await session.commit()

    async def clear(self, scope: OwnerScope) -> None:
        async with self._sessions() as session:
            await configure_session_authorization(session, self._authorization)
            await session.execute(
                delete(ExecutionPoisonedScopeORM).where(
                    ExecutionPoisonedScopeORM.owner_scope_key == scope_key(scope)
                )
            )
            await session.commit()

    async def process_pending(self) -> int:
        # Keep the request row locked until completion. Process death rolls back
        # this claim, leaving a durable pending request for the next worker.
        async with self._sessions() as session:
            await configure_session_authorization(session, self._authorization)
            row = await session.scalar(
                select(ExecutionRecoveryRequestORM)
                .where(
                    ExecutionRecoveryRequestORM.status == "pending",
                )
                .order_by(ExecutionRecoveryRequestORM.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return 0
            prefix, _, identity = row.owner_scope_key.partition(":")
            scope = (
                OwnerScope.team("execution-kernel", identity)
                if prefix == "team"
                else OwnerScope.personal(identity)
            )
            try:
                result, recovered = await rebuild_verified_scope(
                    scope, marker=self, projector=self._projector, source=self._source
                )
                row.result = {
                    "processed_events": result.processed,
                    "recovered_run_ids": [str(item) for item in recovered],
                }
                remaining = (
                    await session.scalars(
                        select(ExecutionPoisonedRunORM.run_id).where(
                            ExecutionPoisonedRunORM.team_id == scope.team_id
                            if scope.team_id
                            else (ExecutionPoisonedRunORM.owner_user_id == scope.user_id)
                            & ExecutionPoisonedRunORM.team_id.is_(None)
                        )
                    )
                ).all()
                row.result = {**row.result, "remaining_run_ids": [str(item) for item in remaining]}
                row.status = "partial" if remaining else "completed"
            except (OSError, RuntimeError, ValueError, SQLAlchemyError) as exc:
                # No payloads or credentials in diagnostics. Scope quarantine is
                # retained by rebuild_verified_scope; a new request can retry.
                row.status = "failed"
                row.result = {"error": type(exc).__name__}
            row.completed_at = datetime.now(UTC)
            await DBAuditRepository(
                session, signing_key=self._audit_key, signing_key_id=self._audit_key_id
            ).add(
                AuditLog(
                    actor_user_id=row.requested_by,
                    action="execution_recovery_" + row.status,
                    resource_type="execution_scope",
                    resource_id=row.owner_scope_key,
                    metadata={"request_id": str(row.id), **row.result},
                )
            )
            await session.commit()
            return 1
