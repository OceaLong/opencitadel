from uuid import uuid4

import pytest

from app.domain.evaluation.configuration import (
    ConfigSelection,
    ConfigVersion,
)


def config(**snapshot):
    return ConfigVersion(
        id=uuid4(),
        entity_id=uuid4(),
        revision=1,
        name="Config",
        selection=ConfigSelection(model_id="model"),
        fingerprint="x",
        snapshot={
            "effective_policy": {"max_iterations": 12},
            "identity": {"model_id": "model"},
            "contract_digest": "old",
            **snapshot,
        },
    )


def test_current_gate_rejects_policy_identity_contract_and_limits_drift():
    from app.application.evaluation.preflight import validate_current_config

    original = config()
    current = dict(original.snapshot)
    assert validate_current_config(original, current) == ()
    for field, value, reason in [
        ("effective_policy", {"max_iterations": 6}, "policy_changed"),
        ("identity", {"model_id": "other"}, "model_changed"),
        ("contract_digest", "new", "contract_changed"),
    ]:
        assert reason in validate_current_config(original, {**current, field: value})
    # Price-only updates must not rewrite or invalidate the pinned model identity.
    assert validate_current_config(original, {**current, "price": {"input": 99}}) == ()


def test_static_contract_reader_never_constructs_tools(monkeypatch):
    from app.application.evaluation.configuration_metadata import builtin_contracts
    from app.domain.services.tools.shell import ShellTool

    def forbidden(*args, **kwargs):
        raise AssertionError("runtime tool path called")

    monkeypatch.setattr(ShellTool, "__init__", forbidden)
    monkeypatch.setattr(ShellTool, "get_tools", forbidden)
    contracts = builtin_contracts(("shell_execute",), mode="agent", allowed_tools=None)
    assert contracts[0]["schema"]["function"]["name"] == "shell_execute"
    assert contracts[0]["schema"]["function"]["parameters"]["properties"]
    with pytest.raises(ValueError, match="contract_unavailable"):
        builtin_contracts(("mcp.unregistered",), mode="agent", allowed_tools=None)


def test_schema_change_has_distinct_digest_without_redefining_old_fingerprint():
    from app.application.evaluation.configuration_metadata import contract_fingerprints

    one = [{"name": "x", "pack": "shell", "policy": {}, "schema": {"type": "string"}}]
    two = [{**one[0], "schema": {"type": "integer"}}]
    legacy1, full1 = contract_fingerprints(one, mode="agent", skill=None)
    legacy2, full2 = contract_fingerprints(two, mode="agent", skill=None)
    assert legacy1 == legacy2
    assert full1 != full2


def test_static_metadata_obeys_production_pack_modes():
    from app.application.evaluation.configuration_metadata import builtin_contracts

    with pytest.raises(ValueError, match="tool_policy_denied"):
        builtin_contracts(("read_file",), mode="ask", allowed_tools=None)
