"""Load and verify the owning Run context for Activity execution."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution.run_context import (
    RunContextUnavailableError,
    run_execution_context,
)
from app.domain.execution.context import RunExecutionContext
from app.domain.execution.run import RunState
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError
from app.infrastructure.execution.models import (
    ExecutionPoisonedScopeORM,
    ExecutionRunProjectionORM,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresRunContextSource:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def load(self, run_id: UUID) -> RunExecutionContext:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            record = await session.get(ExecutionRunProjectionORM, run_id)
            if record is None:
                # Rebuild window (K4-1/P2-14): while any scope's projection is
                # being rebuilt its Run rows are deleted and re-derived, so a
                # missing record is a retryable wait, not a permanent policy
                # failure. The check is deliberately coarse (any rebuilding
                # scope, since only the run_id is known here): rebuilds are
                # rare and short, and the worst case is a few extra deferrals
                # for a genuinely missing Run before it fails as before.
                rebuilding = await session.scalar(
                    select(ExecutionPoisonedScopeORM.owner_scope_key)
                    .where(ExecutionPoisonedScopeORM.rebuilding.is_(True))
                    .limit(1)
                )
                if rebuilding is not None:
                    raise RunContextUnavailableError(
                        f"Run projection {run_id} unavailable: scope rebuild in flight"
                    )
                raise RuntimePolicyIntegrityError("POLICY_SNAPSHOT_INVALID")
        try:
            state = RunState.model_validate(record.state)
            if state.run_id != run_id or canonical_state_hash(state) != record.state_hash:
                raise ValueError("Run projection state mismatch")
            context = run_execution_context(state)
            snapshot = context.policy_snapshot
            if (
                snapshot.execution_revision_id != record.execution_policy_revision_id
                or snapshot.execution_policy_digest != record.execution_policy_digest
            ):
                raise ValueError("Run projection policy metadata mismatch")
            return context
        except (RuntimePolicyIntegrityError, ValueError) as exc:
            raise RuntimePolicyIntegrityError("POLICY_SNAPSHOT_INVALID") from exc


__all__ = ["PostgresRunContextSource"]
