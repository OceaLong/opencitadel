"""Live, bounded validation for one fixed Ops Collector MCP server."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.domain.external.connection_pool import MCPConnectionPoolPort
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.patrol import (
    PatrolObservationSubmission,
    PatrolPackConfig,
    PatrolProbeStatus,
)
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.domain.utils.integration_runtime_builder import mcp_records_to_runtime


class MCPPatrolCollectorValidator:
    """Validate capabilities and execute read-only preflight probes.

    The server record is administrator-owned and the Pack has already rejected
    raw URLs, PromQL, commands, and scripts. Calls therefore remain inside the
    Collector's fixed registered-target contract.
    """

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
            raise ConnectionError(f"Collector connection failed: {manager.connection_errors}")
        advertised = manager.tools.get(server.name, [])
        canonical = await manager.get_all_tools()
        if len(advertised) != len(canonical):
            raise ConnectionError("Collector tool manifest could not be resolved")
        names = {
            source.name: item["function"]["name"]
            for source, item in zip(advertised, canonical, strict=True)
        }
        return manager, names

    @staticmethod
    def _decode(result) -> dict[str, Any]:
        if not result.success:
            raise ConnectionError(result.message or "Collector call failed")
        raw = result.data
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            raise TypeError("Collector returned a non-JSON response")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise TypeError("Collector returned a non-object response")
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
            raise ValueError(f"Collector does not advertise {tool}")
        result = await asyncio.wait_for(
            manager.invoke(canonical, arguments), timeout=timeout_seconds
        )
        return self._decode(result)

    async def get_capabilities(
        self,
        server: MCPServerRecord,
        *,
        policy: ActivityExecutionPolicy,
    ) -> dict[str, Any]:
        manager, names = await self._connect(server, policy=policy)
        return await self._invoke(manager, names, "get_capabilities", {}, timeout_seconds=15)

    async def dry_run(
        self,
        server: MCPServerRecord,
        config: PatrolPackConfig,
        *,
        policy: ActivityExecutionPolicy,
    ) -> dict[str, Any]:
        manager, names = await self._connect(server, policy=policy)
        probes: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for check in config.checks:
            if not check.enabled:
                continue
            args_json = json.dumps(check.probe.args, sort_keys=True, separators=(",", ":"))
            key = (check.probe.tool, args_json)
            if key in seen:
                continue
            seen.add(key)
            envelope = await self._invoke(
                manager,
                names,
                check.probe.tool,
                check.probe.args,
                timeout_seconds=config.defaults.timeout_seconds,
            )
            probes.append(
                {
                    "tool": check.probe.tool,
                    "status": envelope.get("status"),
                    "error_code": envelope.get("error_code"),
                    "request_id": envelope.get("request_id"),
                }
            )
        return {
            "mode": "live-read-only-preflight",
            "ok": all(item["status"] == "ok" for item in probes),
            "probes": probes,
        }

    async def collect(
        self,
        server: MCPServerRecord,
        config: PatrolPackConfig,
        *,
        policy: ActivityExecutionPolicy,
    ) -> list[PatrolObservationSubmission]:
        """Execute the frozen read-only checks and normalize their envelopes."""
        manager, names = await self._connect(server, policy=policy)
        submissions: list[PatrolObservationSubmission] = []
        for check in config.checks:
            if not check.enabled:
                continue
            try:
                envelope = await self._invoke(
                    manager,
                    names,
                    check.probe.tool,
                    check.probe.args,
                    timeout_seconds=config.defaults.timeout_seconds,
                )
                raw_status = str(envelope.get("status") or "error")
                status = (
                    PatrolProbeStatus(raw_status)
                    if raw_status in {item.value for item in PatrolProbeStatus}
                    else PatrolProbeStatus.ERROR
                )
                observation = envelope.get("data")
                submissions.append(
                    PatrolObservationSubmission.model_validate(
                        {
                            "check_id": check.id,
                            "probe_status": status.value,
                            "observation": (observation if isinstance(observation, dict) else {}),
                            "evidence_refs": envelope.get("evidence") or [],
                            "explanation": str(envelope.get("explanation") or ""),
                            "error_code": envelope.get("error_code"),
                            "error_message": envelope.get("error_message"),
                        }
                    )
                )
            except (OSError, RuntimeError, ValueError) as error:
                submissions.append(
                    PatrolObservationSubmission(
                        check_id=check.id,
                        probe_status=PatrolProbeStatus.ERROR,
                        error_code="COLLECTOR_CALL_FAILED",
                        error_message=str(error)[:2000],
                    )
                )
        return submissions
