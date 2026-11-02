"""Public round commitments never export retained origin paths."""

import pytest
from scripts.acceptance.capacity_models import RoundOrigin
from scripts.execution_capacity.reference_round import RoundBinding


def test_private_binding_projects_versioned_complete_origin_without_path():
    binding = RoundBinding(
        parent_attempt_id="parent",
        round_id="round",
        sample_id="sample",
        window_id="window",
        parent_plan_digest="a" * 64,
        child_plan_digest="b" * 64,
        reservation_digest="c" * 64,
        child_path="/private/fixture/rounds/round",
    )
    safe = binding.safe()
    assert safe.schema_version == 1
    assert "/private/fixture" not in safe.model_dump_json()
    assert "child_path" not in safe.model_dump()
    assert RoundOrigin.model_validate(safe.model_dump()) == safe
    assert (
        binding.model_copy(update={"child_path": "/private/foreign/round"})
        .safe()
        .child_origin_sha256
        != safe.child_origin_sha256
    )
    assert (
        binding.model_copy(update={"round_id": "foreign"}).safe().child_origin_sha256
        != safe.child_origin_sha256
    )
    with pytest.raises(ValueError, match=r".+"):
        RoundOrigin.model_validate(binding.model_dump())
    with pytest.raises(ValueError, match=r".+"):
        RoundOrigin.model_validate({**safe.model_dump(), "child_origin_sha256": "wrong"})
