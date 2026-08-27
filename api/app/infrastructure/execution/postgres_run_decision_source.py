"""Read ready Runs from the formal projection for workflow decisions."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution.decision_worker import DecisionCandidate
from app.domain.execution.run import RunState, validated_run_policy_snapshot
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import ExecutionRunProjectionORM
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
            records = tuple(
                (
                    await session.scalars(
                        select(ExecutionRunProjectionORM)
                        .where(ExecutionRunProjectionORM.terminal.is_(False))
                        .order_by(
                            ExecutionRunProjectionORM.updated_at,
                            ExecutionRunProjectionORM.run_id,
                        )
                        .limit(limit)
                    )
                ).all()
            )
        candidates = []
        for record in records:
            state = RunState.model_validate(record.state)
            snapshot = validated_run_policy_snapshot(state)
            if canonical_state_hash(state) != record.state_hash:
                raise ValueError("execution_run_projection state hash mismatch")
            if (
                snapshot.execution_revision_id != record.execution_policy_revision_id
                or snapshot.execution_policy_digest != record.execution_policy_digest
            ):
                raise ValueError("execution_run_projection policy metadata mismatch")
            candidates.append(DecisionCandidate(state=state))
        return tuple(candidates)


__all__ = ["PostgresRunDecisionSource"]
