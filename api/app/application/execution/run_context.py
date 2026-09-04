"""Build verified execution context from formal Run state."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.domain.execution.context import RunExecutionContext
from app.domain.execution.run import RunState, validated_run_policy_snapshot
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError

_INVALID_CONTEXT = "POLICY_SNAPSHOT_INVALID"


class RunContextUnavailableError(Exception):
    """The Run context is temporarily missing (projection rebuild in flight).

    Raised by a context source when the Run projection row is absent *because*
    the scope's formal projection is being rebuilt (K4-1/P2-14). The activity
    worker treats it as a transient fault and defers the claim with backoff
    instead of settling the activity as permanently failed. Defined at the
    application layer so the infrastructure source can raise it and the worker
    can match on it without either importing the other's internals.
    """


class RunContextSource(Protocol):
    async def load(self, run_id: UUID) -> RunExecutionContext: ...


def run_execution_context(state: RunState) -> RunExecutionContext:
    """Fail closed unless state contains one complete admitted Run identity."""
    try:
        if state.family is None or state.correlation_id is None:
            raise ValueError("Run is not created")
        if (state.owner_user_id is None) == (state.team_id is None):
            raise ValueError("Run owner scope is invalid")
        snapshot = validated_run_policy_snapshot(state)
        owner_scope = (
            OwnerScope.personal(state.owner_user_id)
            if state.owner_user_id is not None
            else OwnerScope.team("execution-kernel", state.team_id or "")
        )
        return RunExecutionContext(
            run_id=state.run_id,
            family=state.family,
            owner_scope=owner_scope,
            policy_snapshot=snapshot,
            correlation_id=state.correlation_id,
            source_entity_type=state.source_entity_type,
            source_entity_id=state.source_entity_id,
        )
    except (RuntimePolicyIntegrityError, ValidationError, ValueError) as exc:
        raise RuntimePolicyIntegrityError(_INVALID_CONTEXT) from exc


__all__ = ["RunContextSource", "RunContextUnavailableError", "run_execution_context"]
