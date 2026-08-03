import asyncio

from opencitadel_ops_collector.capabilities import TOOL_INPUT_MODELS, canonical_hash, capability_manifest
from opencitadel_ops_collector.server import create_server


def test_capabilities_have_exact_fixed_tool_surface_and_stable_hash():
    first = capability_manifest("demo")
    second = capability_manifest("demo")
    assert first == second
    assert len(first["enabled_tools"]) == 9
    assert set(first["enabled_tools"]) == set(TOOL_INPUT_MODELS)
    assert first["overall_capability_hash"] == second["overall_capability_hash"]


def test_hash_is_order_independent_and_schema_sensitive():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_server_registers_only_manifest_tools_as_read_only():
    tools = asyncio.run(create_server().list_tools())
    assert {item.name for item in tools} == set(TOOL_INPUT_MODELS)
    for item in tools:
        assert item.annotations.readOnlyHint is True
        assert item.annotations.destructiveHint is False


def test_agent_inputs_contain_no_raw_url_promql_command_or_query():
    tools = asyncio.run(create_server().list_tools())
    forbidden = {"url", "promql", "command", "script", "query"}
    for item in tools:
        assert forbidden.isdisjoint(item.inputSchema.get("properties", {}))
