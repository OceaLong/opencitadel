import asyncio
from types import SimpleNamespace

import pytest

from app.application.services.integration_projection_service import (
    IntegrationConnectionStatus,
    IntegrationProjectionService,
)
from app.domain.models.inference import ResourceVisibility
from app.domain.models.integration_runtime import (
    A2ARuntime,
    A2AServerRuntime,
    MCPRuntime,
    MCPServerRuntime,
    MCPTransport,
)
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from tests.runtime_policy_support import MutablePolicyReader


class _IntegrationService:
    def __init__(self, records, runtime) -> None:
        self.records = records
        self.runtime = runtime

    async def list_servers(self, **_kwargs):
        return self.records

    async def resolve_mcp_runtime(self, *_args, **_kwargs):
        return self.runtime

    async def resolve_a2a_runtime(self, *_args, **_kwargs):
        return self.runtime


class _Pool:
    def __init__(self, cached) -> None:
        self.cached = cached
        self.refreshes = []

    def try_get_cached(self, runtime, *, policy):
        return self.cached

    async def refresh_in_background(self, runtime, *, policy):
        self.refreshes.append((runtime, policy))


class _BackgroundTasks:
    def __init__(self, *, run: bool = True) -> None:
        self.started = []
        self.tasks = []
        self.run = run

    async def start_transient(self, name, factory) -> None:
        self.started.append((name, factory))
        if self.run:
            self.tasks.append(asyncio.create_task(factory()))


@pytest.mark.asyncio
async def test_mcp_projection_exposes_cached_health_and_tools() -> None:
    record = MCPServerRecord(
        id="mcp-1",
        name="tickets",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test",
        visibility=ResourceVisibility.PRIVATE,
    )
    runtime = MCPRuntime(
        servers={
            record.id: MCPServerRuntime(
                id=record.id,
                name=record.name,
                transport=record.transport,
                url=record.url,
            )
        }
    )
    tool = SimpleNamespace(name="post_message", description="Post a message", inputSchema={})
    client = SimpleNamespace(tools={record.name: [tool]}, connection_errors={})
    service = IntegrationProjectionService(
        mcp_servers=_IntegrationService([record], runtime),
        a2a_servers=_IntegrationService([], A2ARuntime()),
        mcp_connection_pool=_Pool(client),
        a2a_connection_pool=_Pool(None),
        policy_reader=MutablePolicyReader(),
        background_tasks=_BackgroundTasks(),
    )

    projected = await service.list_mcp_servers()

    assert projected[0].record.id == "mcp-1"
    assert projected[0].connection_status is IntegrationConnectionStatus.CONNECTED
    assert projected[0].tools[0].name == "post_message"


@pytest.mark.asyncio
async def test_a2a_projection_is_checking_while_cache_warms() -> None:
    record = A2AServerRecord(
        id="a2a-1",
        base_url="https://agent.example.test",
        visibility=ResourceVisibility.PRIVATE,
    )
    runtime = A2ARuntime(servers=(A2AServerRuntime(id=record.id, base_url=record.base_url),))
    a2a_pool = _Pool(None)
    background_tasks = _BackgroundTasks()
    service = IntegrationProjectionService(
        mcp_servers=_IntegrationService([], MCPRuntime()),
        a2a_servers=_IntegrationService([record], runtime),
        mcp_connection_pool=_Pool(None),
        a2a_connection_pool=a2a_pool,
        policy_reader=MutablePolicyReader(),
        background_tasks=background_tasks,
    )

    projected = await service.list_a2a_servers()
    await asyncio.gather(*background_tasks.tasks)

    assert projected[0].connection_status is IntegrationConnectionStatus.CHECKING
    assert len(a2a_pool.refreshes) == 1


@pytest.mark.asyncio
async def test_projection_warmup_is_delegated_to_the_process_task_supervisor() -> None:
    record = A2AServerRecord(
        id="a2a-1",
        base_url="https://agent.example.test",
        visibility=ResourceVisibility.PRIVATE,
    )
    runtime = A2ARuntime(servers=(A2AServerRuntime(id=record.id, base_url=record.base_url),))
    a2a_pool = _Pool(None)
    background_tasks = _BackgroundTasks(run=False)
    service = IntegrationProjectionService(
        mcp_servers=_IntegrationService([], MCPRuntime()),
        a2a_servers=_IntegrationService([record], runtime),
        mcp_connection_pool=_Pool(None),
        a2a_connection_pool=a2a_pool,
        policy_reader=MutablePolicyReader(),
        background_tasks=background_tasks,
    )

    projected = await service.list_a2a_servers()

    assert projected[0].connection_status is IntegrationConnectionStatus.CHECKING
    assert len(background_tasks.started) == 1
    assert a2a_pool.refreshes == []

    _name, factory = background_tasks.started[0]
    await factory()

    assert len(a2a_pool.refreshes) == 1
