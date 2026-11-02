import pytest
from opencitadel_ops_actuator.security import TargetDenied


@pytest.mark.asyncio
async def test_target_identity_is_registered_read_only(make_actuator, fake_k8s):
    actuator = make_actuator()
    target = await actuator.get_target("demo", "api", "deployment")
    assert target == {
        "schema_version": 2,
        "target_ref": actuator.settings.target_ref,
        "namespace": "demo",
        "workload": "api",
        "kind": "deployment",
        "uid": fake_k8s.uid,
    }
    assert fake_k8s.patch_attempts == 0
    assert fake_k8s.core.items == {}
    assert "annotations" not in target


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("namespace", "workload", "kind"),
    [
        ("other", "api", "deployment"),
        ("demo", "unknown", "deployment"),
        ("demo", "api", "statefulset"),
    ],
)
async def test_target_identity_cannot_escape_registration(
    make_actuator, fake_k8s, namespace, workload, kind
):
    with pytest.raises(TargetDenied):
        await make_actuator().get_target(namespace, workload, kind)
    assert fake_k8s.patch_attempts == 0
