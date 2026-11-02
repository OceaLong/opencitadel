"""Registered actions exercise real patch preparation with a CAS workload fake."""

import pytest


@pytest.mark.asyncio
async def test_restart_patches_fixed_annotation_and_reports_before_after(
    make_actuator, invoke, fake_k8s
):
    result = await invoke(make_actuator(), "restart_workload")
    assert result["action_outcome"] == "applied"
    assert fake_k8s.restart_calls == 1
    assert result["after"]["restarted_at"]
    assert result["before"]["generation"] == 1
    assert result["after"]["generation"] == 2
    assert result["receipt"]["target"]["uid"] == fake_k8s.uid
    assert fake_k8s.last_patch["metadata"]["resourceVersion"] == "1"


@pytest.mark.asyncio
async def test_scale_patches_main_resource_with_replicas_and_identity(
    make_actuator, invoke, fake_k8s
):
    result = await invoke(make_actuator(), "scale_workload", replicas=5)
    assert result["action_outcome"] == "applied"
    assert fake_k8s.scale_calls == 1
    assert fake_k8s.restart_calls == 0
    assert result["before"]["replicas"] == 3
    assert result["after"]["replicas"] == 5
    assert fake_k8s.marker == result["operation_id"]
    assert fake_k8s.replicas == 5


@pytest.mark.asyncio
async def test_rollback_uses_previous_template(make_actuator, invoke, fake_k8s):
    result = await invoke(make_actuator(), "rollback_workload")
    assert result["action_outcome"] == "applied"
    assert fake_k8s.rollback_calls == 1
    assert fake_k8s.current_template == "template-v1"
    assert result["after"]["rolled_back_to_revision"] == "1"


@pytest.mark.asyncio
async def test_rollback_without_revision_persists_rejection(make_actuator, invoke, fake_k8s):
    fake_k8s.revisions = ["template-v1"]
    actuator = make_actuator()
    result = await invoke(actuator, "rollback_workload")
    assert result["state"] == "not_applied"
    assert result["error_code"] == "NO_ROLLBACK_REVISION"
    assert (await actuator.get_operation(result["operation_id"]))["receipt"] == result["receipt"]
    assert fake_k8s.patch_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "workload", "kind"),
    [
        ("rollback_workload", "cache", "statefulset"),
        ("restart_workload", "api", "statefulset"),
    ],
)
async def test_kind_mismatch_is_denied(make_actuator, invoke, fake_k8s, action, workload, kind):
    result = await invoke(make_actuator(), action, workload=workload, kind=kind)
    assert result["error_code"] == "KIND_MISMATCH"
    assert fake_k8s.patch_attempts == 0


@pytest.mark.asyncio
async def test_missing_workload_is_definitively_not_applied(make_actuator, invoke, fake_k8s):
    fake_k8s.missing = True
    result = await invoke(make_actuator(), "restart_workload")
    assert result["state"] == "not_applied"
    assert result["error_code"] == "TARGET_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("restart_workload", {}),
        ("scale_workload", {"replicas": 5}),
        ("rollback_workload", {}),
    ],
)
async def test_transport_failure_is_unknown_without_internal_retry(
    make_actuator, invoke, fake_k8s, action, parameters
):
    fake_k8s.raise_on_mutation = ConnectionError("connection reset")
    result = await invoke(make_actuator(), action, **parameters)
    assert result["state"] == "unknown"
    assert result["error_code"] == "OPERATION_UNKNOWN"
    assert fake_k8s.patch_attempts == 1


def test_real_adapter_selects_strategic_patch_wire_type():
    from types import SimpleNamespace

    from opencitadel_ops_actuator.k8s_writer import KubernetesWriter

    calls = []
    writer = object.__new__(KubernetesWriter)
    writer.apps = SimpleNamespace(
        patch_namespaced_deployment=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    body = {"metadata": {"resourceVersion": "7"}, "spec": {"template": {"$patch": "replace"}}}
    writer._patch("demo", "api", "deployment", body)
    assert calls == [
        (
            ("api", "demo", body),
            {
                "_content_type": "application/strategic-merge-patch+json",
                "_request_timeout": (5, 30),
            },
        )
    ]
