"""TDD for the three registered write actions, using FakeKubernetesWriter
(see conftest.py) injected via Actuator's k8s_factory -- the Fake-injection
pattern mirrors ops-collector/tests/test_k8s_probes.py's FakeKubernetes.
"""
import pytest

from opencitadel_ops_actuator.actuator import Actuator


@pytest.mark.asyncio
async def test_restart_patches_restartedAt_annotation_and_reports_before_after(actuator_settings, fake_k8s):
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await actuator.restart_workload("demo", "api", "deployment", "key-1")
    assert result["action_outcome"] == "applied"
    assert fake_k8s.restart_calls == 1
    assert result["after"]["restarted_at"]
    assert result["before"]["generation"] == 1
    assert result["after"]["generation"] == 2


@pytest.mark.asyncio
async def test_scale_patches_main_resource_with_replicas_and_idempotency_key(actuator_settings, fake_k8s):
    """scale must NOT go through the apps/v1 /scale subresource: that
    subresource only round-trips spec.replicas on real clusters and drops
    metadata.annotations, which would silently break the idempotency key.
    It has to be the same single main-resource merge patch as restart/
    rollback (spec.replicas + the remediation-key annotation together)."""
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await actuator.scale_workload("demo", "api", "deployment", 5, "key-1")
    assert result["action_outcome"] == "applied"
    assert fake_k8s.scale_calls == 1
    assert fake_k8s.restart_calls == 0
    assert result["before"]["replicas"] == 3
    assert result["after"]["replicas"] == 5
    # the idempotency marker landed on the same object the replica count did
    assert fake_k8s.marker == "key-1"
    assert fake_k8s.replicas == 5


@pytest.mark.asyncio
async def test_rollback_deployment_reverts_to_previous_replicaset_template(actuator_settings, fake_k8s):
    fake_k8s.revisions = ["template-v1", "template-v2"]
    fake_k8s.current_template = "template-v2"
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await actuator.rollback_workload("demo", "api", "deployment", "key-1")
    assert result["action_outcome"] == "applied"
    assert fake_k8s.rollback_calls == 1
    assert result["before"]["template"] == "template-v2"
    assert result["after"]["template"] == "template-v1"


@pytest.mark.asyncio
async def test_rollback_without_previous_revision_returns_no_rollback_revision(actuator_settings, fake_k8s):
    fake_k8s.revisions = ["template-v1"]
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await actuator.rollback_workload("demo", "api", "deployment", "key-1")
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "NO_ROLLBACK_REVISION"
    assert fake_k8s.rollback_calls == 0


@pytest.mark.asyncio
async def test_statefulset_rollback_rejected_kind_mismatch(actuator_settings, fake_k8s):
    """rollback 仅支持 deployment；statefulset 调 rollback -> KIND_MISMATCH。"""
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await actuator.rollback_workload("demo", "cache", "statefulset", "key-1")
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "KIND_MISMATCH"
    assert fake_k8s.rollback_calls == 0


@pytest.mark.asyncio
async def test_requested_kind_mismatching_registered_target_is_denied(actuator_settings, fake_k8s):
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await actuator.restart_workload("demo", "api", "statefulset", "key-1")
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "KIND_MISMATCH"
    assert fake_k8s.restart_calls == 0


@pytest.mark.asyncio
async def test_missing_workload_maps_to_target_not_found(actuator_settings, fake_k8s):
    fake_k8s.missing = True
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await actuator.restart_workload("demo", "api", "deployment", "key-1")
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "TARGET_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call,call_count_of",
    [
        (lambda actuator: actuator.restart_workload("demo", "api", "deployment", "key-1"), "restart_calls"),
        (lambda actuator: actuator.scale_workload("demo", "api", "deployment", 5, "key-1"), "scale_calls"),
        (lambda actuator: actuator.rollback_workload("demo", "api", "deployment", "key-1"), "rollback_calls"),
    ],
    ids=["restart", "scale", "rollback"],
)
async def test_transient_k8s_error_is_never_retried(actuator_settings, fake_k8s, call, call_count_of):
    """Write actions must never retry: a transient failure (e.g. a dropped
    connection to the API server) is attempted exactly once and reported as
    K8S_ERROR immediately -- retry is a decision for the caller's approval
    chain, not this service (contrast with ops-collector's retry-once-on-
    transient-failure behavior for reads)."""
    fake_k8s.raise_on_mutation = ConnectionError("connection reset by peer")
    actuator = Actuator(actuator_settings(), k8s_factory=lambda: fake_k8s)
    result = await call(actuator)
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "K8S_ERROR"
    assert getattr(fake_k8s, call_count_of) == 1
