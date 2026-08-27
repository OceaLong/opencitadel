"""Fail-closed construction of immutable Run execution context."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.application.execution.run_context import run_execution_context
from app.domain.execution.run import RunFamily, RunState, RunStatus
from app.domain.runtime_policy import RuntimePolicyIntegrityError
from tests.app.execution_test_support import run_policy_snapshot_json

RUN_ID = UUID("ac000000-0000-0000-0000-000000000001")


def _state(**updates: object) -> RunState:
    state = RunState(
        run_id=RUN_ID,
        family=RunFamily.AGENT,
        source_entity_type="session",
        source_entity_id="session-1",
        semantic_payload={},
        policy_snapshot=run_policy_snapshot_json("agent"),
        status=RunStatus.RUNNING,
        stream_version=2,
        owner_user_id="user-1",
        correlation_id=UUID(int=9),
    )
    return state.model_copy(update=updates)


def test_context_contains_only_verified_run_identity_owner_and_snapshot() -> None:
    context = run_execution_context(_state())

    assert context.run_id == RUN_ID
    assert context.family is RunFamily.AGENT
    assert context.owner_scope.user_id == "user-1"
    assert context.policy_snapshot.family is RunFamily.AGENT
    assert context.correlation_id == UUID(int=9)
    with pytest.raises(ValidationError, match="frozen"):
        context.family = RunFamily.ASK
    with pytest.raises(ValidationError, match="frozen"):
        context.owner_scope.user_id = "attacker"


@pytest.mark.parametrize(
    "updates",
    [
        {"policy_snapshot": None},
        {"owner_user_id": None},
        {"correlation_id": None},
    ],
)
def test_incomplete_created_state_is_rejected_with_stable_failure_code(
    updates: dict[str, object],
) -> None:
    with pytest.raises(RuntimePolicyIntegrityError, match="POLICY_SNAPSHOT_INVALID"):
        run_execution_context(_state(**updates))


def test_tampered_snapshot_is_rejected_with_stable_failure_code() -> None:
    snapshot = _state().policy_snapshot
    assert snapshot is not None
    tampered = snapshot.model_copy(update={"snapshot_digest": "sha256:" + "0" * 64})

    with pytest.raises(RuntimePolicyIntegrityError, match="POLICY_SNAPSHOT_INVALID"):
        run_execution_context(_state(policy_snapshot=tampered))
