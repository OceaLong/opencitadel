"""The real fixture client must use the token provisioned for its actuator."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from scripts.drive_remediation_fixture import HarnessError, _call_tool_async


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "short-token"])
async def test_fixture_refuses_missing_or_short_token(monkeypatch, token):
    monkeypatch.setenv("PATROL_ACTUATOR_TOKEN", token)
    with pytest.raises(HarnessError, match="PATROL_ACTUATOR_TOKEN"):
        await _call_tool_async("http://127.0.0.1:18091/mcp", "get_capabilities", {}, attempts=0)


@pytest.mark.asyncio
async def test_fixture_authenticates_the_mcp_transport(monkeypatch):
    token = "fixture-generated-token-for-auth-proof-123456"
    monkeypatch.setenv("PATROL_ACTUATOR_TOKEN", token)
    captured = {}

    @asynccontextmanager
    async def transport(**kwargs):
        captured.update(kwargs)
        yield object(), object(), object()

    class Session:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def initialize(self):
            pass

        async def call_tool(self, name, arguments):
            return SimpleNamespace(isError=False, structuredContent={"name": name})

    monkeypatch.setattr("mcp.client.streamable_http.streamablehttp_client", transport)
    monkeypatch.setattr("mcp.ClientSession", Session)
    result = await _call_tool_async("http://127.0.0.1:18091/mcp", "get_capabilities", {})
    assert result == {"name": "get_capabilities"}
    assert captured["headers"] == {"Authorization": f"Bearer {token}"}
