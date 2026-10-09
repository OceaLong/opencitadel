"""Validate terminal missing-usage accounting retention, never physical unknown cleanup."""

import hashlib
import json
from decimal import Decimal
from uuid import UUID


def validate_accounting(value, *, batch_id, owner_id):
    if value.get("batch_id") != batch_id or value.get("owner_id") != owner_id:
        raise ValueError("foreign accounting evidence")
    UUID(batch_id)
    if value.get("batch_status") != "completed" or value.get("cleanup_status") != "clean":
        raise ValueError("batch or environment not terminal clean")
    if value.get("pending_effects") != 0 or value.get("unsettled_calls") != 0:
        raise ValueError("unknown physical outcome is not retained accounting")
    leases = value.get("environment_leases", [])
    if not leases or any(row.get("state") != "verified_clean" for row in leases):
        raise ValueError("actual environment cleanup is not verified")
    for row in leases:
        UUID(row["id"])
    calls = value.get("calls", [])
    if not calls or len({row["call_identity"] for row in calls}) != len(calls):
        raise ValueError("missing or duplicate physical calls")
    missing, known_judge = [], []
    expected = {}
    for call in calls:
        UUID(call["call_identity"])
        UUID(call["run_id"])
        UUID(call["activity_id"])
        if call["state"] != "settled" or not call.get("settled_at") or not call.get("settlement"):
            raise ValueError("active or unknown reservation")
        if (
            call["fact"].get("call_identity") != call["call_identity"]
            or call["demand"].get("batch_id") != batch_id
        ):
            raise ValueError("foreign physical call")
        if (
            call["demand"].get("scope") != "user:" + owner_id
            or call["demand"].get("requester") != owner_id
        ):
            raise ValueError("foreign accounting principal")
        fact, settlement, demand = call["fact"], call["settlement"], call["demand"]
        digest = hashlib.sha256(
            json.dumps(
                fact, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
        if settlement.get("evidence") != digest:
            raise ValueError("settlement fact digest mismatch")
        purpose = call["purpose"]
        if demand.get("purpose") != purpose:
            raise ValueError("purpose mismatch")
        if any(
            Decimal(str(call["price"].get(key) or 0)) <= 0
            for key in ("input_per_million", "output_per_million")
        ):
            raise ValueError("positive configured price control required")
        unknown = fact.get("cost_usd") is None
        if unknown:
            if (
                purpose != "evaluation_subject"
                or settlement.get("tokens") is not None
                or settlement.get("money") is not None
            ):
                raise ValueError("not the expected missing subject usage")
            if any(
                fact["usage"].get(key) is not None
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            ):
                raise ValueError("missing-usage fixture has token evidence")
            missing.append(call["call_identity"])
        elif purpose == "evaluation_judge":
            if settlement.get("tokens") is None or settlement.get("money") is None:
                raise ValueError("judge not independently accounted")
            known_judge.append(call["call_identity"])
        key = f"6:purpose:{batch_id}:{purpose}"
        if key not in {bucket["key"] for bucket in demand["buckets"]}:
            raise ValueError("missing exact batch purpose bucket")
        tokens, money = expected.setdefault(key, [0, Decimal(0)])
        expected[key] = [
            tokens + (demand.get("tokens") or 0) * (settlement.get("tokens") is None),
            money + Decimal(str(demand.get("money") or 0)) * (settlement.get("money") is None),
        ]
    if not missing or not known_judge:
        raise ValueError("missing subject and positive judge controls required")
    buckets = value.get("buckets", [])
    if len(buckets) != len(expected) or {row["key"] for row in buckets} != set(expected):
        raise ValueError("accounting bucket coverage mismatch")
    for row in buckets:
        tokens, money = expected[row["key"]]
        if (
            row["slots"] != 0
            or row["reserved_tokens"] != tokens
            or Decimal(str(row["reserved_money"])) != money
        ):
            raise ValueError("active occupancy or unexplained retained hold")
    if not any(tokens > 0 or money > 0 for tokens, money in expected.values()):
        raise ValueError("no retained accounting obligation")
    return {
        "status": "retained-accounting",
        "environment_status": "clean",
        "physical_slots": 0,
        "missing_subject_calls": missing,
        "known_judge_calls": known_judge,
        "bucket_holds": [
            {"key": key, "tokens": tokens, "money": str(money)}
            for key, (tokens, money) in sorted(expected.items())
        ],
    }
