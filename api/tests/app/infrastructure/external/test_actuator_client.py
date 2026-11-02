import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.integration_server import MCPServerRecord
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.infrastructure.external.actuator_client import MCPActuatorClient
from tests.app.patrol_actuator_support import capabilities


def client_fixture():
    requirements = {
        "get_capabilities": [],
        "get_target": ["namespace", "workload", "kind"],
        "get_operation": ["operation_id"],
        **{
            tool: [
                "namespace",
                "workload",
                "kind",
                "operation_id",
                "payload_digest",
                "target_uid",
                "idempotency_key",
            ]
            + (["replicas"] if tool == "scale_workload" else [])
            for tool in ("restart_workload", "scale_workload", "rollback_workload")
        },
    }
    tools = [
        SimpleNamespace(name=name, inputSchema={"required": fields})
        for name, fields in requirements.items()
    ]
    manifest = capabilities()
    manifest.pop("overall_capability_hash")
    manifest["overall_capability_hash"] = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manager = SimpleNamespace(
        connection_errors={},
        tools={"ops-actuator": tools},
        get_all_tools=AsyncMock(
            return_value=[{"function": {"name": "actuator__" + name}} for name in requirements]
        ),
        invoke=AsyncMock(return_value=SimpleNamespace(success=True, data=manifest)),
    )
    client = MCPActuatorClient(SimpleNamespace(acquire=AsyncMock(return_value=manager)))
    server = MCPServerRecord(id="actuator", name="ops-actuator", url="http://actuator.invalid/mcp")
    return client, server, manager, manifest


@pytest.mark.asyncio
async def test_actual_tools_and_capability_manifest_must_support_durable_identity():
    client, server, manager, manifest = client_fixture()
    assert await client.get_capabilities(server, policy=ActivityExecutionPolicy()) == manifest
    manager.tools[server.name][-1].inputSchema["required"].remove("target_uid")
    with pytest.raises(ValueError, match="required durable identity"):
        await client.get_capabilities(server, policy=ActivityExecutionPolicy())


@pytest.mark.asyncio
async def test_manifest_tampering_is_rejected():
    client, server, _manager, manifest = client_fixture()
    manifest["target_ref"] = "different-cluster"
    with pytest.raises(ValueError, match="digest mismatch"):
        await client.get_capabilities(server, policy=ActivityExecutionPolicy())


@pytest.mark.asyncio
async def test_query_invokes_only_advertised_get_operation():
    client, server, manager, _ = client_fixture()
    manager.invoke.return_value.data = {"state": "unknown"}
    assert await client.get_operation(server, "operation-1", policy=ActivityExecutionPolicy()) == {
        "state": "unknown"
    }
    manager.invoke.assert_awaited_once_with(
        "actuator__get_operation", {"operation_id": "operation-1"}
    )


@pytest.mark.asyncio
async def test_transport_timeout_never_retries_write():
    client, server, manager, _ = client_fixture()
    manager.invoke.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await client.execute_action(
            server,
            "restart_workload",
            {"operation_id": "operation-1"},
            policy=ActivityExecutionPolicy(),
        )
    manager.invoke.assert_awaited_once()
