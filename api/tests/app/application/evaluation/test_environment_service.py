from uuid import uuid4

import pytest

from app.domain.evaluation.environment import (
    CaseSlot,
    EnvironmentLease,
    ImageIdentity,
    reusable,
    transition,
)


def lease():
    return EnvironmentLease(
        id=uuid4(),
        environment_version=uuid4(),
        case_slot=CaseSlot(
            workspace="user:alice",
            batch_id=uuid4(),
            case_id=uuid4(),
            config_version=uuid4(),
            repeat=1,
        ),
        generation=1,
        revision=1,
        state="allocated",
    )


def test_quarantined_environment_is_not_reusable():
    assert reusable("quarantine") is False
    assert reusable("verified_clean") is True
    assert not any(reusable(s) for s in ("allocated", "preparing", "ready", "leased", "cleaning"))


def test_lifecycle_failure_requires_explicit_repair_and_verify():
    item = transition(lease(), "preparing")
    item = transition(item, "quarantine")
    with pytest.raises(ValueError, match="invalid_environment_transition"):
        transition(item, "verified_clean")
    with pytest.raises(PermissionError):
        transition(item, "cleaning", repair=True, administrator=False)
    item = transition(item, "cleaning", repair=True, administrator=True)
    assert item.repair_authorized
    assert transition(item, "verified_clean").state == "verified_clean"


def test_namespaces_distinguish_every_case_config_repeat_and_generation():
    a = lease()
    values = {a.namespace}
    for key, value in [
        ("case_id", uuid4()),
        ("config_version", uuid4()),
        ("batch_id", uuid4()),
        ("repeat", 2),
    ]:
        b = a.model_copy(update={"case_slot": a.case_slot.model_copy(update={key: value})})
        values.add(b.namespace)
    values.add(a.model_copy(update={"generation": 2}).namespace)
    assert len(values) == 6


def test_image_identity_never_interprets_local_id_as_registry_digest():
    value = "sha256:" + "a" * 64
    assert ImageIdentity(kind="local_content_id", value=value).kind == "local_content_id"
    with pytest.raises(ValueError, match="String should match pattern"):
        ImageIdentity(kind="registry_digest", value="latest")
