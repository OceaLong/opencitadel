from unittest.mock import AsyncMock

import pytest

from app.domain.models.inference import ResourceVisibility
from app.domain.models.integration_runtime import MCPTransport
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole
from app.interfaces.endpoints.integration_routes import (
    create_mcp_server,
    delete_a2a_server,
    delete_mcp_server,
    list_a2a_servers,
    list_mcp_servers,
    set_a2a_server_enabled,
    set_mcp_server_enabled,
    update_a2a_server,
    update_mcp_server,
)
from app.interfaces.schemas.integration import (
    CreateMCPServerRequest,
    SetIntegrationEnabledRequest,
    UpdateA2AServerRequest,
    UpdateMCPServerRequest,
)
from tests.app.openapi_test_support import app


def _context(*, admin: bool = False) -> WorkspaceContext:
    principal = Principal(
        user_id="admin-1" if admin else "user-1",
        global_role=GlobalRole.ADMIN if admin else GlobalRole.USER,
    )
    return WorkspaceContext(
        principal=principal,
        scope=OwnerScope.personal(principal.user_id),
    )


def test_integration_routes_use_first_class_prefix_and_stable_ids() -> None:
    paths = app.openapi()["paths"]

    assert set(paths["/api/integrations/mcp-servers"]) == {"get", "post"}
    assert set(paths["/api/integrations/mcp-servers/{server_id}"]) == {
        "put",
        "delete",
    }
    assert set(paths["/api/integrations/mcp-servers/{server_id}/enabled"]) == {"patch"}
    assert set(paths["/api/integrations/a2a-servers"]) == {"get", "post"}
    assert set(paths["/api/integrations/a2a-servers/{server_id}"]) == {
        "put",
        "delete",
    }
    assert set(paths["/api/integrations/a2a-servers/{server_id}/enabled"]) == {"patch"}
    assert not any(path.startswith("/api/app-config/") for path in paths)


@pytest.mark.asyncio
async def test_integration_lists_are_passive_registry_reads() -> None:
    mcp = MCPServerRecord(
        id="mcp-1",
        name="docs",
        url="https://mcp.example.test",
    )
    a2a = A2AServerRecord(id="a2a-1", base_url="https://agent.example.test")
    mcp_service = AsyncMock()
    mcp_service.list_servers.return_value = [mcp]
    a2a_service = AsyncMock()
    a2a_service.list_servers.return_value = [a2a]
    ctx = _context()

    mcp_response = await list_mcp_servers(ctx=ctx, service=mcp_service)
    a2a_response = await list_a2a_servers(ctx=ctx, service=a2a_service)

    mcp_service.list_servers.assert_awaited_once_with(scope=ctx.scope)
    a2a_service.list_servers.assert_awaited_once_with(scope=ctx.scope)
    assert mcp_response.data.items[0].id == "mcp-1"
    assert a2a_response.data.items[0].id == "a2a-1"
    assert "connection_status" not in mcp_response.data.items[0].model_dump()
    assert "tools" not in mcp_response.data.items[0].model_dump()


@pytest.mark.asyncio
async def test_mcp_mutations_use_path_id_and_authenticated_actor() -> None:
    service = AsyncMock()
    service.create_server.side_effect = lambda record, **_: record
    service.update_server.side_effect = lambda _, record, **__: record
    service.set_enabled.return_value = MCPServerRecord(
        id="stable-mcp-id",
        name="docs-renamed",
        url="https://mcp.example.test/v2",
        enabled=False,
        visibility=ResourceVisibility.GLOBAL,
    )
    ctx = _context(admin=True)
    create = CreateMCPServerRequest(
        name="docs",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test",
        visibility=ResourceVisibility.GLOBAL,
    )

    await create_mcp_server(create, ctx=ctx, service=service)
    created = service.create_server.await_args.args[0]
    assert created.id
    assert created.name == "docs"
    assert service.create_server.await_args.kwargs["actor_user_id"] == "admin-1"

    update = UpdateMCPServerRequest(
        name="docs-renamed",
        transport=MCPTransport.STREAMABLE_HTTP,
        enabled=True,
        url="https://mcp.example.test/v2",
        visibility=ResourceVisibility.GLOBAL,
    )
    await update_mcp_server("stable-mcp-id", update, ctx=ctx, service=service)
    assert service.update_server.await_args.args[0] == "stable-mcp-id"
    assert service.update_server.await_args.args[1].id == "stable-mcp-id"

    await set_mcp_server_enabled(
        "stable-mcp-id",
        SetIntegrationEnabledRequest(enabled=False),
        ctx=ctx,
        service=service,
    )
    service.set_enabled.assert_awaited_with(
        "stable-mcp-id",
        False,
        scope=ctx.scope,
        actor_user_id="admin-1",
        is_admin=True,
    )
    await delete_mcp_server("stable-mcp-id", ctx=ctx, service=service)
    assert service.delete_server.await_args.args[0] == "stable-mcp-id"


@pytest.mark.asyncio
async def test_a2a_update_delete_and_enable_use_path_id() -> None:
    service = AsyncMock()
    service.update_server.side_effect = lambda _, record, **__: record
    service.set_enabled.return_value = A2AServerRecord(
        id="stable-a2a-id",
        base_url="https://agent.example.test/v2",
        enabled=False,
        visibility=ResourceVisibility.PRIVATE,
    )
    ctx = _context()
    update = UpdateA2AServerRequest(
        base_url="https://agent.example.test/v2",
        enabled=True,
        visibility=ResourceVisibility.PRIVATE,
    )

    await update_a2a_server("stable-a2a-id", update, ctx=ctx, service=service)
    assert service.update_server.await_args.args[0] == "stable-a2a-id"
    assert service.update_server.await_args.args[1].id == "stable-a2a-id"
    await set_a2a_server_enabled(
        "stable-a2a-id",
        SetIntegrationEnabledRequest(enabled=False),
        ctx=ctx,
        service=service,
    )
    await delete_a2a_server("stable-a2a-id", ctx=ctx, service=service)
    assert service.set_enabled.await_args.args[:2] == ("stable-a2a-id", False)
    assert service.delete_server.await_args.args[0] == "stable-a2a-id"
