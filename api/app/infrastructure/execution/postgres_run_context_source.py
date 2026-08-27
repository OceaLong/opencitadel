"""Load and verify the owning Run context for Activity execution."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution.run_context import run_execution_context
from app.domain.execution.context import RunExecutionContext
from app.domain.execution.run import RunState
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError
from app.infrastructure.execution.models import ExecutionRunProjectionORM
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
