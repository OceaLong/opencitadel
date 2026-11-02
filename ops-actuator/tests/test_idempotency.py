"""A retained journal receipt, not the target's current annotation, owns deduplication."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_same_key_replays_original_receipt(make_actuator, invoke, fake_k8s):
    first = await invoke(make_actuator(), "restart_workload")
    second = await invoke(make_actuator(), "restart_workload")
    assert first["action_outcome"] == "applied"
    assert second["action_outcome"] == "skipped_idempotent"
    assert second["receipt"] == first["receipt"]
    assert fake_k8s.restart_calls == 1


@pytest.mark.asyncio
async def test_a_b_a_uses_persisted_receipt(make_actuator, invoke, fake_k8s):
    first = await invoke(make_actuator(), "restart_workload", key="a")
    await invoke(make_actuator(), "restart_workload", key="b")
    third = await invoke(make_actuator(), "restart_workload", key="a")
    assert third["receipt"] == first["receipt"]
    assert fake_k8s.restart_calls == 2
    assert fake_k8s.marker == "operation:b"


@pytest.mark.asyncio
async def test_missing_key_rejected(make_actuator, invoke, fake_k8s):
    result = await invoke(make_actuator(), "restart_workload", key="")
    assert result["error_code"] == "IDEMPOTENCY_KEY_MISSING"
    assert fake_k8s.patch_attempts == 0


@pytest.mark.asyncio
async def test_same_key_scale_payload_conflict(make_actuator, invoke, fake_k8s):
    first = await invoke(make_actuator(), "scale_workload", replicas=7)
    conflict = await invoke(make_actuator(), "scale_workload", replicas=8)
    assert conflict["error_code"] == "OPERATION_CONFLICT"
    assert fake_k8s.scale_calls == 1
    assert (await make_actuator().get_operation(first["operation_id"]))["receipt"] == first[
        "receipt"
    ]


@pytest.mark.asyncio
async def test_same_operation_cannot_be_rebound_to_new_key(make_actuator, invoke, fake_k8s):
    await invoke(make_actuator(), "restart_workload", key="a", operation_id="op")
    conflict = await invoke(make_actuator(), "restart_workload", key="b", operation_id="op")
    assert conflict["error_code"] == "OPERATION_CONFLICT"
    assert fake_k8s.restart_calls == 1


@pytest.mark.asyncio
async def test_concurrent_instances_share_cas_identity(make_actuator, invoke, fake_k8s):
    results = await asyncio.gather(
        *[invoke(make_actuator(), "restart_workload") for _ in range(12)]
    )
    resolved = await make_actuator().get_operation("operation:key-1")
    assert resolved["state"] == "applied"
    assert fake_k8s.restart_calls == 1
    assert all(r["operation_id"] == resolved["operation_id"] for r in results)
    receipts = [r["receipt"] for r in results if r["receipt"]]
    assert all(r == resolved["receipt"] for r in receipts)
