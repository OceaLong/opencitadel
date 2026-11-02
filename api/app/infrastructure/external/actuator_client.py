"""Live, bounded execution against the fixed Ops Actuator MCP server.

Connection mechanics mirror
app/application/services/patrol_collector_validator.py:25-62 (acquire a pool
connection scoped to a single registered MCPServerRecord, resolve advertised
tool names, decode the JSON envelope). Unlike the Collector validator this
client also exposes the three registered write actions
(restart_workload/scale_workload/rollback_workload), never retries a write on
failure (retry decisions belong to the caller's approval chain — see
ops-actuator/src/opencitadel_ops_actuator/server.py docstring), and never
accepts a caller-supplied idempotency_key for reuse beyond a single explicit
argument (PatrolRemediationService.execute() always passes the persisted
PatrolRemediation.idempotency_key, never an LLM- or batch-executor-derived
value).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from app.domain.external.connection_pool import MCPConnectionPoolPort
from app.domain.models.integration_server import MCPServerRecord
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.domain.utils.integration_runtime_builder import mcp_records_to_runtime

# The bounded write-action surface registered by ops-actuator/server.py.
# get_capabilities is intentionally excluded here (see get_capabilities()).
ACTUATOR_ACTION_TOOLS = frozenset({"restart_workload", "scale_workload", "rollback_workload"})


class MCPActuatorClient:
    def __init__(self, connection_pool: MCPConnectionPoolPort) -> None:
        self._connection_pool = connection_pool

    async def _connect(
        self,
        server: MCPServerRecord,
        *,
        policy: ActivityExecutionPolicy,
    ):
        manager = await self._connection_pool.acquire(
            mcp_records_to_runtime([server]),
            policy=policy,
        )
        if manager.connection_errors:
            raise ConnectionError(f"Actuator connection failed: {manager.connection_errors}")
        advertised = manager.tools.get(server.name, [])
        canonical = await manager.get_all_tools()
        if len(advertised) != len(canonical):
            raise ConnectionError("Actuator tool manifest could not be resolved")
        names = {
            source.name: item["function"]["name"]
            for source, item in zip(advertised, canonical, strict=True)
        }
        return manager, names

    @staticmethod
    def _decode(result) -> dict[str, Any]:
        if not result.success:
            raise ConnectionError(result.message or "Actuator call failed")
        raw = result.data
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            raise TypeError("Actuator returned a non-JSON response")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise TypeError("Actuator returned a non-object response")
        return decoded

    async def _invoke(
        self,
        manager,
        names: dict[str, str],
        tool: str,
        arguments: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        canonical = names.get(tool)
        if canonical is None:
            raise ValueError(f"Actuator does not advertise {tool}")
        result = await asyncio.wait_for(
            manager.invoke(canonical, arguments), timeout=timeout_seconds
        )
        return self._decode(result)

    async def get_capabilities(
        self,
        server: MCPServerRecord,
        *,
        policy: ActivityExecutionPolicy,
        timeout_seconds: int = 15,
    ) -> dict[str, Any]:
        manager, names = await self._connect(server, policy=policy)
        manifest = await self._invoke(
            manager,
            names,
            "get_capabilities",
            {},
            timeout_seconds=timeout_seconds,
        )
        effect = manifest.get("effect_contract") or {}
        if (
            manifest.get("schema_version") != 2
            or effect.get("version") != 2
            or effect.get("execute") is not True
            or effect.get("resolve") != "get_operation"
            or effect.get("target_identity") != "get_target"
            or not isinstance(manifest.get("target_ref"), str)
            or not manifest["target_ref"]
        ):
            raise ValueError("Actuator durable effect capabilities unavailable")
        core = {key: value for key, value in manifest.items() if key != "overall_capability_hash"}
        expected = hashlib.sha256(
            json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if manifest.get("overall_capability_hash") != expected:
            raise ValueError("Actuator capability manifest digest mismatch")
        required_tools = ACTUATOR_ACTION_TOOLS | {"get_target", "get_operation"}
        if (
            not required_tools <= set(manifest.get("enabled_tools", []))
            or not required_tools <= names.keys()
        ):
            raise ValueError("Actuator durable tools are not advertised")
        for source in manager.tools.get(server.name, []):
            required = {
                "get_target": {"namespace", "workload", "kind"},
                "get_operation": {"operation_id"},
            }.get(source.name)
            if source.name in ACTUATOR_ACTION_TOOLS:
                required = {
                    "namespace",
                    "workload",
                    "kind",
                    "idempotency_key",
                    "operation_id",
                    "payload_digest",
                    "target_uid",
                }
                if source.name == "scale_workload":
                    required.add("replicas")
            if required is not None and not required <= set(source.inputSchema.get("required", [])):
                raise ValueError("Actuator tool schema omits required durable identity")
        return manifest

    async def get_target(
        self,
        server: MCPServerRecord,
        *,
        namespace: str,
        workload: str,
        kind: str,
        policy: ActivityExecutionPolicy,
        timeout_seconds: int = 15,
    ) -> dict[str, Any]:
        manager, names = await self._connect(server, policy=policy)
        return await self._invoke(
            manager,
            names,
            "get_target",
            {"namespace": namespace, "workload": workload, "kind": kind},
            timeout_seconds,
        )

    async def get_operation(
        self,
        server: MCPServerRecord,
        operation_id: str,
        *,
        policy: ActivityExecutionPolicy,
        timeout_seconds: int = 15,
    ) -> dict[str, Any]:
        """Query the original provider journal; never invokes an action tool."""
        manager, names = await self._connect(server, policy=policy)
        return await self._invoke(
            manager, names, "get_operation", {"operation_id": operation_id}, timeout_seconds
        )

    async def execute_action(
        self,
        server: MCPServerRecord,
        tool: str,
        arguments: dict[str, Any],
        *,
        policy: ActivityExecutionPolicy,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Invoke one of the three registered write actions exactly once.

        Never retried here: a write action that times out or errors is
        reported back to the caller as-is (action_outcome == "failed" is
        decoded from the envelope like any other outcome); retrying a write
        is an approval-chain decision, not a transport concern.
        """
        if tool not in ACTUATOR_ACTION_TOOLS:
            raise ValueError(f"unsupported actuator action {tool!r}")
        manager, names = await self._connect(server, policy=policy)
        return await self._invoke(
            manager,
            names,
            tool,
            arguments,
            timeout_seconds=timeout_seconds,
        )
