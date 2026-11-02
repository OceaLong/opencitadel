"""G4 requires actual MCP servers, API-server CAS and namespace-scoped RBAC."""

import asyncio

import pytest
from opencitadel_ops_actuator.k8s_writer import OPERATION_ANNOTATION


async def settle(fixture, operation_id, *, instance=1):
    for _ in range(20):
        result = await fixture.call("get_operation", {"operation_id": operation_id}, instance)
        if result["state"] in {"applied", "not_applied", "unknown"}:
            return result
        await asyncio.sleep(0.1)
    return result


@pytest.mark.asyncio
async def test_concurrent_processes_share_operation(live_actuator):
    args = await live_actuator.arguments()
    results = await asyncio.gather(
        *(live_actuator.call("restart_workload", args, instance) for instance in (0, 1))
    )
    final = await settle(live_actuator, args["operation_id"])
    assert final["state"] == "applied", (results, final)
    assert len(live_actuator.proxy.patches) == 1
    assert live_actuator.proxy.patches[0]["status"] == 200
    assert live_actuator.proxy.patches[0]["body"]["metadata"]["uid"] == args["target_uid"]
    assert live_actuator.proxy.patches[0]["body"]["metadata"]["resourceVersion"]


@pytest.mark.asyncio
async def test_same_key_different_payload_is_rejected(live_actuator):
    first = await live_actuator.arguments(replicas=0)
    assert (await live_actuator.call("scale_workload", first))["state"] == "applied"
    changed = await live_actuator.arguments(key=first["idempotency_key"], replicas=1)
    conflict = await live_actuator.call("scale_workload", changed, 1)
    assert conflict["error_code"] == "OPERATION_CONFLICT"
    assert len(live_actuator.proxy.patches) == 1


@pytest.mark.asyncio
async def test_applied_response_loss_recovers_after_restart(live_actuator):
    args = await live_actuator.arguments()
    await live_actuator.crash_after_patch(args)
    # A fresh process has no memory of the call. The actual target marker and
    # durable journal are the only sources for the retained provider receipt.
    restarted = live_actuator.start_process()
    recovered = await settle(live_actuator, args["operation_id"], instance=restarted)
    assert recovered["state"] == "applied"
    assert recovered["receipt"]["operation_id"] == args["operation_id"]
    again = await live_actuator.call("restart_workload", args, restarted)
    assert again["receipt"] == recovered["receipt"]
    assert len(live_actuator.proxy.patches) == 1


@pytest.mark.asyncio
async def test_a_b_a_uses_retained_receipt(live_actuator):
    first = await live_actuator.arguments()
    a = await live_actuator.call("restart_workload", first)
    assert a["state"] == "applied"
    second = await live_actuator.arguments()
    b = await live_actuator.call("restart_workload", second, 1)
    assert b["state"] == "applied"
    repeated = await live_actuator.call("restart_workload", first)
    assert repeated["receipt"] == a["receipt"]
    assert (
        live_actuator.target()["metadata"]["annotations"][OPERATION_ANNOTATION]
        == second["operation_id"]
    )
    assert len(live_actuator.proxy.patches) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["marker", "recreated", "read_denied"])
async def test_missing_marker_or_recreated_target_is_unknown(live_actuator, loss):
    args = await live_actuator.arguments()
    await live_actuator.crash_after_patch(args)
    if loss == "marker":
        live_actuator.kubectl(
            "-n",
            live_actuator.namespace,
            "annotate",
            "deployment/target",
            OPERATION_ANNOTATION + "-",
        )
    elif loss == "recreated":
        live_actuator.kubectl(
            "-n", live_actuator.namespace, "delete", "deployment", "target", "--wait=true"
        )
        live_actuator.apply(live_actuator.deployment())
        assert live_actuator.target()["metadata"]["uid"] != args["target_uid"]
    else:
        live_actuator.kubectl("-n", live_actuator.namespace, "delete", "rolebinding", "target")
    result = await settle(live_actuator, args["operation_id"])
    assert result["state"] == "unknown"
    assert result["receipt"] is None
    assert len(live_actuator.proxy.patches) == 1


@pytest.mark.asyncio
async def test_journal_unavailable_fails_before_patch(live_actuator):
    args = await live_actuator.arguments()
    live_actuator.journal_role(False)
    result = await live_actuator.call("restart_workload", args)
    assert result["error_code"] == "OPERATION_JOURNAL_UNAVAILABLE"
    assert live_actuator.proxy.patches == []
    live_actuator.journal_role(True)
    assert (await live_actuator.call("restart_workload", args))["state"] == "applied"
    assert len(live_actuator.proxy.patches) == 1
