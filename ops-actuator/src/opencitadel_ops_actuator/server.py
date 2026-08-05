"""FastMCP registration for the four fixed operations.

Structure mirrors ops-collector/src/opencitadel_ops_collector/server.py.
Unlike the Collector (every tool annotated READ_ONLY), the three write
tools here are annotated readOnlyHint=False, destructiveHint=True so
callers cannot mistake restart/scale/rollback for safe, repeatable reads.
"""
from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .actuator import Actuator
from .capabilities import capability_manifest
from .config import ActuatorSettings


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def create_server(settings: ActuatorSettings | None = None) -> FastMCP:
    cfg = settings or ActuatorSettings()
    actuator = Actuator(cfg)
    server = FastMCP(
        "OpenCitadel Ops Actuator",
        instructions=(
            "Fixed, bounded, registered write actions for Kubernetes workloads. "
            "Every write tool requires an idempotency_key and is never retried "
            "automatically on failure; retry decisions belong to the caller's "
            "approval chain."
        ),
        host=cfg.host,
        port=cfg.port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )

    @server.tool(name="get_capabilities", annotations=READ_ONLY)
    async def get_capabilities() -> dict:
        """Return deterministic tool and schema hashes without mutating any target."""
        return capability_manifest(cfg.target_ref)

    @server.tool(name="restart_workload", annotations=WRITE)
    async def restart_workload(
        namespace: str,
        workload: str,
        kind: Literal["deployment", "statefulset"],
        idempotency_key: str,
    ) -> dict:
        """Roll a registered Deployment or StatefulSet by patching its pod template restart annotation."""
        return await actuator.restart_workload(namespace, workload, kind, idempotency_key)

    @server.tool(name="scale_workload", annotations=WRITE)
    async def scale_workload(
        namespace: str,
        workload: str,
        kind: Literal["deployment", "statefulset"],
        replicas: int,
        idempotency_key: str,
    ) -> dict:
        """Scale a registered Deployment or StatefulSet to a replica count within its registered bounds."""
        return await actuator.scale_workload(namespace, workload, kind, replicas, idempotency_key)

    @server.tool(name="rollback_workload", annotations=WRITE)
    async def rollback_workload(
        namespace: str,
        workload: str,
        kind: Literal["deployment", "statefulset"],
        idempotency_key: str,
    ) -> dict:
        """Roll a registered Deployment back to its previous ReplicaSet template. StatefulSets are not supported."""
        return await actuator.rollback_workload(namespace, workload, kind, idempotency_key)

    return server
