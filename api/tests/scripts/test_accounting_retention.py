import hashlib
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from scripts.acceptance.accounting_retention import validate_accounting


def evidence():
    batch, owner = str(uuid4()), "owned-user"
    calls, buckets = [], []
    for purpose, unknown in (("evaluation_subject", True), ("evaluation_judge", False)):
        identity = str(uuid4())
        key = f"6:purpose:{batch}:{purpose}"
        calls.append(
            {
                "call_identity": identity,
                "run_id": str(uuid4()),
                "activity_id": str(uuid4()),
                "purpose": purpose,
                "price": {"input_per_million": "1", "output_per_million": "2"},
                "state": "settled",
                "settled_at": "2026-09-17T01:00:00Z",
                "demand": {
                    "batch_id": batch,
                    "scope": "user:" + owner,
                    "requester": owner,
                    "purpose": purpose,
                    "tokens": 4096,
                    "money": "0.1",
                    "buckets": [{"key": key}],
                },
                "settlement": {
                    "evidence": "a" * 64,
                    **({} if unknown else {"tokens": 10, "money": "0.001"}),
                },
                "fact": {
                    "call_identity": identity,
                    "cost_usd": None if unknown else "0.001",
                    "usage": {
                        "prompt_tokens": None if unknown else 5,
                        "completion_tokens": None if unknown else 5,
                        "total_tokens": None if unknown else 10,
                    },
                },
            }
        )
        buckets.append(
            {
                "key": key,
                "slots": 0,
                "reserved_tokens": 4096 if unknown else 0,
                "reserved_money": "0.1" if unknown else "0",
            }
        )
    for call in calls:
        call["settlement"]["evidence"] = hashlib.sha256(
            json.dumps(
                call["fact"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    return {
        "batch_id": batch,
        "owner_id": owner,
        "batch_status": "completed",
        "cleanup_status": "clean",
        "environment_leases": [{"id": str(uuid4()), "state": "verified_clean"}],
        "pending_effects": 0,
        "unsettled_calls": 0,
        "calls": calls,
        "buckets": buckets,
    }


def validate(value):
    return validate_accounting(value, batch_id=value["batch_id"], owner_id=value["owner_id"])


def test_retention_keeps_conservative_amounts_without_claiming_unknown_physical_cleanup():
    value = evidence()
    original = deepcopy(value)
    result = validate(value)
    assert result["status"] == "retained-accounting"
    assert result["physical_slots"] == 0
    assert result["missing_subject_calls"] == [value["calls"][0]["call_identity"]]
    assert value == original


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(batch_status="running"),
        lambda v: v.update(cleanup_status="pending"),
        lambda v: v.update(pending_effects=1),
        lambda v: v.update(environment_leases=[]),
        lambda v: v["environment_leases"][0].update(state="quarantine"),
        lambda v: v.update(unsettled_calls=1),
        lambda v: v["calls"][0].update(state="unknown"),
        lambda v: v["calls"][0].update(settlement=None),
        lambda v: v["buckets"][0].update(slots=1),
        lambda v: v["buckets"][0].update(reserved_tokens=0),
        lambda v: v["calls"][0]["demand"].update(batch_id=str(uuid4())),
        lambda v: v["calls"][0]["demand"].update(requester="foreign"),
        lambda v: v["calls"].pop(),
        lambda v: v["calls"].append(deepcopy(v["calls"][0])),
    ],
)
def test_unresolved_foreign_or_forged_zero_residue_is_rejected(change):
    value = evidence()
    change(value)
    with pytest.raises(
        ValueError,
        match=r"batch|environment|physical|reservation|occupancy|foreign|calls|required|settlement",
    ):
        validate(value)


def test_settlement_must_bind_the_exact_immutable_usage_fact():
    value = evidence()
    value["calls"][0]["settlement"]["evidence"] = "f" * 64
    with pytest.raises(ValueError, match="settlement fact"):
        validate(value)
