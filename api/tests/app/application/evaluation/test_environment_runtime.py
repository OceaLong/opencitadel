from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.execution.agent_tool_catalog import AgentToolCatalog


@pytest.mark.asyncio
async def test_marked_isolation_missing_binding_never_enters_ordinary_catalog():
    catalog = object.__new__(AgentToolCatalog)
    catalog._build = AsyncMock(side_effect=AssertionError("ordinary catalog discovered"))
    context = SimpleNamespace(run=SimpleNamespace(source_entity_type="evaluation_isolated_case"))
    for action in (
        lambda: catalog.definitions({}, context),
        lambda: catalog.invoke({}, context, name="shell_execute", arguments={}),
        lambda: catalog.retrieve({}, context, query="test"),
    ):
        with pytest.raises(ValueError, match="environment_binding_missing"):
            await action()
    catalog._build.assert_not_called()


@pytest.mark.asyncio
async def test_isolated_catalog_before_discovery_and_recorded_dispatch_preserved():
    isolated = SimpleNamespace(
        active=AsyncMock(return_value=True),
        definitions=AsyncMock(return_value="isolated"),
        invoke=AsyncMock(return_value={"success": True}),
        retrieval=AsyncMock(return_value={"query": "test", "sources": []}),
    )
    catalog = object.__new__(AgentToolCatalog)
    catalog._isolated = isolated
    catalog._build = AsyncMock(side_effect=AssertionError("ordinary catalog discovered"))
    context = SimpleNamespace(run=SimpleNamespace(source_entity_type="evaluation_isolated_case"))
    assert await catalog.definitions({}, context) == "isolated"
    assert (await catalog.invoke({}, context, name="shell_execute", arguments={}))["success"]
    assert (await catalog.retrieve({}, context, query="test"))["sources"] == []
    catalog._replay = SimpleNamespace(
        active=AsyncMock(return_value=True), definitions=AsyncMock(return_value="recorded")
    )
    assert await catalog.definitions({}, context) == "recorded"
    catalog._build.assert_not_called()


def test_inventory_cannot_register_arbitrary_production_target_or_credential():
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment import TestTarget

    known = TestTarget(
        id=uuid4(),
        physical_resource="owned-fixture",
        kind="http",
        endpoint="http://allowed.e04.test:8081",
    )
    registry = AdapterRegistry(targets=(known,))
    registry.qualify("target", known)
    with pytest.raises(ValueError, match="inventory_binding"):
        registry.qualify(
            "target", known.model_copy(update={"endpoint": "https://production.example"})
        )


def test_selected_external_contract_cannot_be_rebound_to_another_environment_or_policy():
    from app.application.evaluation.configuration_metadata import ExternalToolContract
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.models.tool_policy import CONSERVATIVE_TOOL_POLICY

    environment_id = uuid4()
    contract = ExternalToolContract(
        name="mcp_echo",
        pack="mcp",
        schema_body={"type": "function"},
        policy=CONSERVATIVE_TOOL_POLICY,
        connector_id="fixture",
        binding_revision="1",
        authority_revision="1",
    )
    target = SimpleNamespace(id=uuid4(), contracts=(contract,))
    registry = AdapterRegistry(executors=((target.id, object()),))
    expected = contract.model_dump(mode="json")
    expected["schema"] = expected.pop("schema_body")
    config = SimpleNamespace(
        selection=SimpleNamespace(
            tool_names=(contract.name,),
            resources=(),
            external_contract_ref=SimpleNamespace(kind="environment", version_id=environment_id),
        ),
        snapshot={"contracts": [expected]},
    )
    adapter = SimpleNamespace(tool_names=())
    registry.validate_tools(adapter, config, (target,), environment_id=environment_id)
    config.selection.external_contract_ref.kind = "recording"
    with pytest.raises(ValueError, match="environment_reference_mismatch"):
        registry.validate_tools(adapter, config, (target,), environment_id=environment_id)
    config.selection.external_contract_ref.kind = "environment"
    config.snapshot["contracts"][0] = {**expected, "policy": {}}
    with pytest.raises(ValueError, match="environment_external_contract_changed"):
        registry.validate_tools(adapter, config, (target,), environment_id=environment_id)
