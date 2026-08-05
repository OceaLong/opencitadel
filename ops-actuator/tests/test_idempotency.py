"""TDD for opencitadel.io/remediation-key annotation-based idempotent write
dedup. A repeated call with the same idempotency_key must be answered from
the current observed state without a second mutating k8s call.
"""
import pytest

from opencitadel_ops_actuator.actuator import Actuator


@pytest.mark.asyncio
async def test_same_idempotency_key_second_call_is_skipped_idempotent(actuator_settings, fake_k8s):
    """同 key 二次调用 -> action_outcome=skipped_idempotent，k8s patch 只发生一次。"""
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    first = await actuator.restart_workload("demo", "api", "deployment", "key-1")
    second = await actuator.restart_workload("demo", "api", "deployment", "key-1")
    assert first["action_outcome"] == "applied"
    assert second["action_outcome"] == "skipped_idempotent"
    assert fake_k8s.restart_calls == 1
    # skip path reports the current observed snapshot; it need not carry the
    # transient restarted_at marker that only the applied restart() result has.
    assert second["after"]["generation"] == first["after"]["generation"]
    assert second["after"]["replicas"] == first["after"]["replicas"]


@pytest.mark.asyncio
async def test_different_key_executes_again(actuator_settings, fake_k8s):
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    first = await actuator.restart_workload("demo", "api", "deployment", "key-1")
    second = await actuator.restart_workload("demo", "api", "deployment", "key-2")
    assert first["action_outcome"] == "applied"
    assert second["action_outcome"] == "applied"
    assert fake_k8s.restart_calls == 2


@pytest.mark.asyncio
async def test_missing_key_rejected(actuator_settings, fake_k8s):
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await actuator.restart_workload("demo", "api", "deployment", "")
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "IDEMPOTENCY_KEY_MISSING"
    assert fake_k8s.restart_calls == 0


@pytest.mark.asyncio
async def test_same_key_scale_is_also_skipped_idempotent(actuator_settings, fake_k8s):
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    first = await actuator.scale_workload("demo", "api", "deployment", 7, "key-1")
    second = await actuator.scale_workload("demo", "api", "deployment", 7, "key-1")
    assert first["action_outcome"] == "applied"
    assert second["action_outcome"] == "skipped_idempotent"
    assert fake_k8s.scale_calls == 1
