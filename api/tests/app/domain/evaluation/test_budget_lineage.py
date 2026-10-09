from uuid import uuid4

import pytest


def test_model_round_identity_survives_run_retry_and_distinguishes_planned_rounds():
    from app.application.evaluation.physical_lineage import logical_model_invocation
    from app.application.execution.decisions.base import activity_identity
    from app.domain.execution.run import RunFamily, RunState
    from tests.app.execution_test_support import run_policy_snapshot_json

    # The policy is the same validated policy used by ActivityWorker tests.
    state = RunState(
        run_id=uuid4(),
        family=RunFamily.AGENT,
        policy_snapshot=run_policy_snapshot_json(RunFamily.AGENT),
        status="running",
    )
    first = activity_identity(state, "model:0")
    second = activity_identity(state, "model:1")
    state = state.model_copy(
        update={"requested_activities": ((first, "model.call", 0), (second, "model.call", 0))}
    )
    key0 = logical_model_invocation(state, first, 0)
    key1 = logical_model_invocation(state, second, 0)
    assert key0 != key1
    retried = state.model_copy(update={"retry_generation": 1})
    replacement = activity_identity(retried, "model:0")
    retried = retried.model_copy(
        update={
            "requested_activities": (*state.requested_activities, (replacement, "model.call", 1))
        }
    )
    assert logical_model_invocation(retried, replacement, 1) == key0
    with pytest.raises(ValueError, match="lineage"):
        logical_model_invocation(retried, first, 0)
    with pytest.raises(ValueError, match="lineage"):
        logical_model_invocation(retried, uuid4(), 1)
