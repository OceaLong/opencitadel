from importlib import import_module

import pytest


def _canonical_module():
    return import_module("app.domain.runtime_policy.canonical")


def test_digest_uses_stable_canonical_json() -> None:
    canonical = _canonical_module()

    first = canonical.policy_digest(1, {"b": 2, "a": 1})
    second = canonical.policy_digest(1, {"a": 1, "b": 2})

    assert first == second
    assert first == "sha256:b74f0ed44e729f531b35c2cf3b2d5a4f1394e848250d9d66c3cf4f7d559f315e"


def test_schema_version_changes_digest() -> None:
    canonical = _canonical_module()

    assert canonical.policy_digest(2, {"a": 1, "b": 2}) == (
        "sha256:504e6a686094dfdf27eefe9e55dcfd9989029c3aef7dfa6c89792f420a788d49"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonicalization_rejects_non_finite_numbers(value: float) -> None:
    canonical = _canonical_module()

    with pytest.raises(ValueError, match="finite"):
        canonical.canonical_policy_bytes(1, {"ratio": value})


def test_model_and_json_payload_have_identical_digest() -> None:
    policy_module = import_module("app.domain.runtime_policy")
    canonical = _canonical_module()
    policy = policy_module.ExecutionPolicy()

    assert canonical.policy_digest(1, policy) == canonical.policy_digest(
        1,
        policy.model_dump(mode="json"),
    )
