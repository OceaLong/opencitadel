from uuid import UUID

from app.domain.execution.registry import EventPayloads
from app.domain.execution.run import RunAggregate, RunState


def test_claim_schema_preserves_baseline_and_old_unknown():
    aggregate = RunAggregate()
    for name in (
        "MarkActivityCallStarted",
        "CompleteActivity",
        "FailActivity",
        "MarkActivityOutcomeUnknown",
    ):
        assert aggregate.command_registry.latest_version(name) == (
            3 if name == "FailActivity" else 2
        )
    version, payload = aggregate.event_registry.upcast(
        "ActivityCallStarted",
        1,
        EventPayloads(public={"activity_id": str(UUID(int=2)), "generation": 0}, internal={}),
    )
    assert version == 2
    assert payload.public["claim_generation"] is None


def test_second_claim_start_is_a_fact_and_duplicate_is_not():
    aggregate = RunAggregate()
    aid = UUID(int=2)
    state = RunState(
        run_id=UUID(int=1),
        active_activity_ids=(aid,),
        activity_generations=((aid, 0),),
        started_activity_ids=(aid,),
        started_activity_claims=((aid, 0, 1),),
    )
    model = aggregate.command_registry.latest("MarkActivityCallStarted").model
    second = model(activity_id=aid, generation=0, claim_generation=2)
    assert len(aggregate._decide_MarkActivityCallStarted(state, second).events) == 1
    first = model(activity_id=aid, generation=0, claim_generation=1)
    assert not aggregate._decide_MarkActivityCallStarted(state, first).events


def test_old_failure_upcasts_without_inventing_drift_evidence():
    aggregate = RunAggregate()
    for version in (1, 2):
        payload = {"activity_id": str(UUID(int=2)), "generation": 0, "failure_code": "FAILED"}
        if version == 2:
            payload["claim_generation"] = 1
        latest, upgraded = aggregate.event_registry.upcast(
            "ActivityFailed", version, EventPayloads(public=payload, internal={})
        )
        assert latest == 3
        assert upgraded.public["public_data"] == {}


def test_request_v2_captures_generation_and_known_parent_without_redefining_v1():
    aggregate = RunAggregate()
    assert aggregate.command_registry.latest_version("RequestActivity") == 4
    _, old = aggregate.event_registry.upcast(
        "ActivityRequested",
        1,
        EventPayloads(
            public={
                "activity_id": str(UUID(int=2)),
                "activity_type": "tool.call",
                "timeout_at": "2026-09-07T00:00:00Z",
                "input_digest": "abc",
            },
            internal={},
        ),
    )
    assert old.public["generation"] is None
    assert old.public["parent_activity_id"] is None
    assert old.public["invocation_id"] is None
    assert old.public["subject_digest"] is None
