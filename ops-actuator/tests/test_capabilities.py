"""Mirrors ops-collector/tests/test_capabilities.py, adapted for the
actuator's 6-tool surface (capabilities, recovery + 3 registered write
actions) and its non-read-only annotations.
"""

import asyncio

from opencitadel_ops_actuator.capabilities import (
    TOOL_INPUT_MODELS,
    canonical_hash,
    capability_manifest,
)
from opencitadel_ops_actuator.server import create_server


def test_manifest_has_six_tools_and_deterministic_hash():
    first = capability_manifest("demo")
    second = capability_manifest("demo")
    assert first == second
    assert len(first["enabled_tools"]) == 6
    assert set(first["enabled_tools"]) == set(TOOL_INPUT_MODELS)
    assert first["overall_capability_hash"] == second["overall_capability_hash"]


def test_hash_is_order_independent_and_schema_sensitive():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_write_tools_are_not_readonly_annotated():
    """3 个写工具的 MCP annotation readOnlyHint 必须为 False -- 区别于 collector 全 READ_ONLY。"""
    tools = asyncio.run(create_server().list_tools())
    by_name = {item.name: item for item in tools}
    assert by_name["get_capabilities"].annotations.readOnlyHint is True
    assert by_name["get_capabilities"].annotations.destructiveHint is False
    for name in ("restart_workload", "scale_workload", "rollback_workload"):
        assert by_name[name].annotations.readOnlyHint is False
        assert by_name[name].annotations.destructiveHint is True


def test_input_schemas_require_idempotency_key():
    """3 个写工具 input schema 的 required 含 idempotency_key。"""
    tools = asyncio.run(create_server().list_tools())
    by_name = {item.name: item for item in tools}
    for name in ("restart_workload", "scale_workload", "rollback_workload"):
        assert "idempotency_key" in by_name[name].inputSchema.get("required", [])


def test_manifest_declares_durable_recovery_and_input_binding():
    manifest = capability_manifest("demo")
    assert manifest["effect_contract"]["resolve"] == "get_operation"
    assert manifest["effect_contract"]["max_journal_bytes"] == 512 * 1024
    tools = {item.name: item for item in asyncio.run(create_server().list_tools())}
    assert tools["get_operation"].annotations.readOnlyHint is True
    assert tools["get_target"].annotations.readOnlyHint is True
    assert manifest["effect_contract"]["target_identity"] == "get_target"
    for name in ("restart_workload", "scale_workload", "rollback_workload"):
        assert {"operation_id", "payload_digest", "target_uid", "idempotency_key"} <= set(
            tools[name].inputSchema["required"]
        )
