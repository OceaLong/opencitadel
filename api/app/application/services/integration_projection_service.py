"""Read-side health projections for first-class Integration resources."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.application.ports.background_tasks import (
    BackgroundTaskFactory,
    BackgroundTaskSupervisorPort,
)
from app.application.services.integration_server_service import (
    A2AIntegrationService,
    MCPServerService,
)
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.external.connection_pool import (
    A2AConnectionPoolPort,
    MCPConnectionPoolPort,
)
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.scope import OwnerScope
from app.domain.utils.time_utils import utc_now


class IntegrationConnectionStatus(StrEnum):
    CONNECTED = "connected"
    CHECKING = "checking"
    ERROR = "error"
    DISABLED = "disabled"
    POLICY_UNAVAILABLE = "policy_unavailable"


class MCPToolProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str | None = None
    input_schema: dict[str, Any]


class MCPServerProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: MCPServerRecord
    tools: tuple[MCPToolProjection, ...] = ()
    connection_status: IntegrationConnectionStatus
    connection_error: str | None = None


class A2AServerProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: A2AServerRecord
    agent_card: dict[str, Any] | None = None
    connection_status: IntegrationConnectionStatus
    connection_error: str | None = None


class IntegrationProjectionService:
    def __init__(
        self,
        *,
        mcp_servers: MCPServerService,
        a2a_servers: A2AIntegrationService,
        mcp_connection_pool: MCPConnectionPoolPort,
        a2a_connection_pool: A2AConnectionPoolPort,
        policy_reader: PolicyHeadReader,
        background_tasks: BackgroundTaskSupervisorPort,
    ) -> None:
        self._mcp_servers = mcp_servers
        self._a2a_servers = a2a_servers
        self._mcp_pool = mcp_connection_pool
        self._a2a_pool = a2a_connection_pool
        self._policy_reader = policy_reader
        self._background_tasks = background_tasks
        self._warming: set[str] = set()

    async def _warm(self, name: str, factory: BackgroundTaskFactory) -> None:
        if name in self._warming:
            return
        self._warming.add(name)

        async def run() -> None:
            try:
                await factory()
            finally:
                self._warming.discard(name)

        try:
            await self._background_tasks.start_transient(name, run)
        except BaseException:
            self._warming.discard(name)
            raise

    async def list_mcp_servers(
        self,
        scope: OwnerScope | None = None,
    ) -> list[MCPServerProjection]:
        records = await self._mcp_servers.list_servers(scope=scope)
        runtime = await self._mcp_servers.resolve_mcp_runtime(scope)
        try:
            active = await self._policy_reader.active_execution(
                require_fresh=True,
                now=utc_now(),
            )
        except (OSError, RuntimeError, ValueError):
            return [
                MCPServerProjection(
                    record=record,
                    connection_status=(
                        IntegrationConnectionStatus.DISABLED
                        if not record.enabled
                        else IntegrationConnectionStatus.POLICY_UNAVAILABLE
                    ),
                )
                for record in records
            ]

        policy = active.revision.policy.activity
        manager = self._mcp_pool.try_get_cached(runtime, policy=policy)
        if manager is None and any(record.enabled for record in records):
            await self._warm(
                _warm_task_name("mcp", runtime, policy),
                lambda: self._mcp_pool.refresh_in_background(runtime, policy=policy),
            )

        projections: list[MCPServerProjection] = []
        for record in records:
            if not record.enabled:
                status = IntegrationConnectionStatus.DISABLED
                error = None
                tools = ()
            elif manager is None:
                status = IntegrationConnectionStatus.CHECKING
                error = None
                tools = ()
            else:
                error = manager.connection_errors.get(record.name)
                status = (
                    IntegrationConnectionStatus.ERROR
                    if error
                    else IntegrationConnectionStatus.CONNECTED
                )
                tools = tuple(
                    MCPToolProjection(
                        name=str(tool.name),
                        description=(str(tool.description) if tool.description else None),
                        input_schema=dict(tool.inputSchema or {}),
                    )
                    for tool in manager.tools.get(record.name, [])
                )
            projections.append(
                MCPServerProjection(
                    record=record,
                    tools=tools,
                    connection_status=status,
                    connection_error=error,
                )
            )
        return projections

    async def list_a2a_servers(
        self,
        scope: OwnerScope | None = None,
    ) -> list[A2AServerProjection]:
        records = await self._a2a_servers.list_servers(scope=scope)
        runtime = await self._a2a_servers.resolve_a2a_runtime(scope)
        try:
            active = await self._policy_reader.active_execution(
                require_fresh=True,
                now=utc_now(),
            )
        except (OSError, RuntimeError, ValueError):
            return [
                A2AServerProjection(
                    record=record,
                    connection_status=(
                        IntegrationConnectionStatus.DISABLED
                        if not record.enabled
                        else IntegrationConnectionStatus.POLICY_UNAVAILABLE
                    ),
                )
                for record in records
            ]

        policy = active.revision.policy.activity
        manager = self._a2a_pool.try_get_cached(runtime, policy=policy)
        if manager is None and any(record.enabled for record in records):
            await self._warm(
                _warm_task_name("a2a", runtime, policy),
                lambda: self._a2a_pool.refresh_in_background(runtime, policy=policy),
            )

        projections: list[A2AServerProjection] = []
        for record in records:
            card = manager.agent_cards.get(record.id) if manager is not None else None
            if not record.enabled:
                status = IntegrationConnectionStatus.DISABLED
                error = None
            elif manager is None:
                status = IntegrationConnectionStatus.CHECKING
                error = None
            elif card is None:
                status = IntegrationConnectionStatus.ERROR
                error = "Agent Card unavailable"
            else:
                status = IntegrationConnectionStatus.CONNECTED
                error = None
            projections.append(
                A2AServerProjection(
                    record=record,
                    agent_card=dict(card) if card is not None else None,
                    connection_status=status,
                    connection_error=error,
                )
            )
        return projections


def _warm_task_name(kind: str, runtime: BaseModel, policy: BaseModel) -> str:
    material = f"{runtime.model_dump_json()}:{policy.model_dump_json()}".encode()
    digest = hashlib.sha256(material).hexdigest()[:16]
    return f"integration-projection-{kind}-{digest}"


__all__ = [
    "A2AServerProjection",
    "IntegrationConnectionStatus",
    "IntegrationProjectionService",
    "MCPServerProjection",
    "MCPToolProjection",
]
