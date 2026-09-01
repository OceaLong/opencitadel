"""Read ready Runs from the formal projection for workflow decisions."""

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution.decision_worker import DecisionCandidate
from app.domain.execution.run import RunState, validated_run_policy_snapshot
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionPoisonedRunORM,
    ExecutionRunProjectionORM,
)
from app.infrastructure.observability.execution_metrics import record_poisoned_run
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresRunDecisionSource:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def load_ready(self, *, limit: int) -> tuple[DecisionCandidate, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            # Already-quarantined Runs are excluded so a poison row is skipped on
            # every subsequent scan instead of re-poisoning the batch forever.
            quarantined = select(ExecutionPoisonedRunORM.run_id)
            records = tuple(
                (
                    await session.scalars(
                        select(ExecutionRunProjectionORM)
                        .where(
                            ExecutionRunProjectionORM.terminal.is_(False),
                            ExecutionRunProjectionORM.run_id.not_in(quarantined),
                        )
                        .order_by(
                            ExecutionRunProjectionORM.updated_at,
                            ExecutionRunProjectionORM.run_id,
                        )
                        .limit(limit)
                    )
                ).all()
            )
            candidates: list[DecisionCandidate] = []
            quarantined_any = False
            for record in records:
                try:
                    candidate = self._decode(record)
                except (ValidationError, ValueError) as exc:
                    # Poison-row isolation: one corrupt projection must not abort
                    # the whole batch (which previously crashed every replica).
                    # Quarantine it, count it, and keep processing the rest.
                    await self._quarantine(session, record, exc)
                    record_poisoned_run()
                    quarantined_any = True
                    continue
                candidates.append(candidate)
            if quarantined_any:
                await session.commit()
        return tuple(candidates)

    @staticmethod
    def _decode(record: ExecutionRunProjectionORM) -> DecisionCandidate:
        state = RunState.model_validate(record.state)
        snapshot = validated_run_policy_snapshot(state)
        if canonical_state_hash(state) != record.state_hash:
            raise ValueError("execution_run_projection state hash mismatch")
        if (
            snapshot.execution_revision_id != record.execution_policy_revision_id
            or snapshot.execution_policy_digest != record.execution_policy_digest
        ):
            raise ValueError("execution_run_projection policy metadata mismatch")
        return DecisionCandidate(state=state)

    @staticmethod
    async def _quarantine(
        session: AsyncSession,
        record: ExecutionRunProjectionORM,
        error: Exception,
    ) -> None:
        now = datetime.now(UTC)
        reason = type(error).__name__
        detail = str(error)[:2000] or reason
        statement = (
            pg_insert(ExecutionPoisonedRunORM)
            .values(
                run_id=record.run_id,
                owner_user_id=record.owner_user_id,
                team_id=record.team_id,
                reason=reason[:128],
                last_error=detail,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_update(
                index_elements=["run_id"],
                set_={
                    "reason": reason[:128],
                    "last_error": detail,
                    "last_seen_at": now,
                },
            )
        )
        await session.execute(statement)


__all__ = ["PostgresRunDecisionSource"]
