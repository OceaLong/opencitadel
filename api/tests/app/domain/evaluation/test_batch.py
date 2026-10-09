from uuid import uuid4

import pytest


def test_same_slot_redelivery_is_same_admission():
    from app.domain.evaluation.batch import admission_key

    assert admission_key("b", "c", "v", 0, 0) == 'evaluation:["b","c","v",0,0]'
    assert admission_key("b", "c", "v", 0, 0) != admission_key("b", "c", "v", 0, 1)


def test_seeded_schedule_interleaves_configs_and_is_bounded():
    from app.domain.evaluation.batch import schedule_slots

    cases, configs = [uuid4() for _ in range(1000)], [uuid4() for _ in range(5)]
    slots = schedule_slots(cases, configs, 1, 12)
    assert len(slots) == 5000
    assert slots == schedule_slots(cases, configs, 1, 12)
    assert len(set(slots)) == 5000
    for start in range(0, 5000, 5):
        assert {s.config_version_id for s in slots[start : start + 5]} == set(configs)
        assert len({s.case_revision_id for s in slots[start : start + 5]}) == 1
    with pytest.raises(ValueError, match="matrix exceeds 5000"):
        schedule_slots(cases, configs, 2, 12)


def test_state_machine_keeps_execution_and_quality_distinct():
    from app.domain.evaluation.batch import aggregate_status

    assert aggregate_status("running", ["waiting", "running"], ["pending", "pending"]) == "running"
    assert aggregate_status("running", ["waiting"], ["pending"]) == "waiting"
    assert aggregate_status("running", ["succeeded"], ["running"]) == "running"
    assert aggregate_status("running", ["succeeded"], ["mismatch"]) == "completed"
    assert (
        aggregate_status("running", ["blocked_budget", "succeeded"], ["skipped", "complete"])
        == "completed_with_errors"
    )
    assert (
        aggregate_status("cancelling", ["unknown", "cancelled"], ["skipped", "skipped"])
        == "cancelled"
    )
    assert aggregate_status("cancelling", ["running"], ["pending"]) == "cancelling"
    assert aggregate_status("cancelled", ["succeeded"], ["complete"]) == "cancelled"


def test_retry_never_automatically_repeats_unknown_or_quality_failure():
    from app.domain.evaluation.batch import automatic_retry_allowed, manual_retry_allowed

    assert automatic_retry_allowed("failed", 1, infrastructure=True, unknown=False)
    assert not automatic_retry_allowed("failed", 2, infrastructure=True, unknown=False)
    assert not automatic_retry_allowed("failed", 0, infrastructure=True, unknown=True)
    assert not automatic_retry_allowed("mismatch", 0, infrastructure=False, unknown=False)
    assert manual_retry_allowed("succeeded", "mismatch", resources_available=True, unknown=False)
    assert not manual_retry_allowed("unknown", "pending", resources_available=True, unknown=True)


def test_pending_automatic_scoring_keeps_batch_running():
    from app.domain.evaluation.batch import aggregate_status

    assert aggregate_status("running", ["succeeded"], ["pending"]) == "running"


def test_dispatchable_pending_remains_queued_while_active_scoring_wins():
    from app.domain.evaluation.batch import aggregate_status

    assert aggregate_status("queued", ["queued", "waiting"], ["pending", "pending"]) == "queued"
    assert aggregate_status("running", ["queued", "succeeded"], ["pending", "pending"]) == "running"


@pytest.mark.parametrize("current", ["running", "waiting"])
def test_begun_batch_never_returns_to_queued_for_replacement(current):
    from app.domain.evaluation.batch import aggregate_status

    assert aggregate_status(current, ["queued"], ["pending"]) == "running"
