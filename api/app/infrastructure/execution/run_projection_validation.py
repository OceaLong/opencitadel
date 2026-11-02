"""Shared trusted projection decoding; no event replay on the readiness path."""

from app.domain.execution.run import TERMINAL_STATUSES, RunState, validated_run_policy_snapshot
from app.domain.execution.serialization import canonical_state_hash


def decode_run_projection(record) -> RunState:
    state = RunState.model_validate(record.state)
    if canonical_state_hash(state) != record.state_hash:
        raise ValueError("execution_run_projection state hash mismatch")
    if (
        state.run_id != record.run_id
        or (state.owner_user_id, state.team_id) != (record.owner_user_id, record.team_id)
        or state.family is None
        or state.family.value != record.family
        or state.source_entity_type != record.source_entity_type
        or state.source_entity_id != record.source_entity_id
        or state.stream_version != record.stream_version
        or state.status.value != record.status
        or (state.status in TERMINAL_STATUSES) != record.terminal
        or len(state.active_activity_ids) != record.active_activity_count
        or state.wait_reason != record.wait_reason
        or record.last_event_position <= 0
    ):
        raise ValueError("execution_run_projection identity or position metadata mismatch")
    if (state.definition_pin is None) != (state.definition_manifest is None):
        raise ValueError("run projection definition closure is incomplete")
    if state.definition_manifest is not None:
        state.definition_manifest.require_pin(state.definition_pin)
        if state.definition_manifest.family != state.family:
            raise ValueError("run projection definition family mismatch")
    expected_digest = state.definition_pin.manifest_digest if state.definition_pin else None
    if record.definition_digest != expected_digest:
        raise ValueError("run projection definition routing digest mismatch")
    snapshot = validated_run_policy_snapshot(state)
    if (
        snapshot.execution_revision_id != record.execution_policy_revision_id
        or snapshot.execution_policy_digest != record.execution_policy_digest
    ):
        raise ValueError("execution_run_projection policy metadata mismatch")
    return state
