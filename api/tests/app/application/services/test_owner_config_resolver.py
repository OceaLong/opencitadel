#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.errors.exceptions import BadRequestError, ForbiddenError
from app.application.services.integration_server_service import (
    MCPServerService,
    _apply_masked_secret_updates,
    _ensure_stdio_allowed,
)
from app.application.services.owner_config_resolver import (
    merge_configs,
    validate_user_override_payload,
)
from app.domain.models.app_config import AgentConfig, AppConfig, MCPTransport, MemoryConfig, ServerConfig
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.llm_model import ResourceVisibility
from app.domain.models.scope import OwnerScope


def test_merge_configs_applies_user_override_sections_only():
    global_cfg = AppConfig(
        agent_config=AgentConfig(max_iterations=100),
        memory=MemoryConfig(recall_limit=20),
        server=ServerConfig(rate_limit_per_minute=120),
    )
    override_payload = {
        "agent_config": AgentConfig(max_iterations=50).model_dump(mode="json"),
    }
    merged = merge_configs(global_cfg, override_payload)
    assert merged.agent_config.max_iterations == 50
    assert merged.memory.recall_limit == 20
    assert merged.server.rate_limit_per_minute == 120


def test_merge_configs_unmodified_sections_follow_global_updates():
    global_cfg = AppConfig(
        agent_config=AgentConfig(max_iterations=100),
        memory=MemoryConfig(recall_limit=20),
    )
    override_payload = {
        "agent_config": AgentConfig(max_iterations=50).model_dump(mode="json"),
    }
    merged = merge_configs(global_cfg, override_payload)
    assert merged.agent_config.max_iterations == 50
    assert merged.memory.recall_limit == 20

    global_cfg.memory = MemoryConfig(recall_limit=99)
    merged_again = merge_configs(global_cfg, override_payload)
    assert merged_again.memory.recall_limit == 99


def test_validate_user_override_payload_rejects_global_sections():
    with pytest.raises(BadRequestError):
        validate_user_override_payload({"server": {"rate_limit_per_minute": 10}})


def test_ensure_stdio_allowed_blocks_non_admin():
    record = MCPServerRecord(
        id="1",
        name="local",
        transport=MCPTransport.STDIO,
        command="npx",
    )
    with pytest.raises(ForbiddenError):
        _ensure_stdio_allowed(record, is_admin=False)


def test_ensure_stdio_allowed_allows_admin():
    record = MCPServerRecord(
        id="1",
        name="local",
        transport=MCPTransport.STDIO,
        command="npx",
    )
    _ensure_stdio_allowed(record, is_admin=True)


def test_apply_masked_secret_updates_per_key():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"API_KEY": "****", "OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"API_KEY": "secret", "OTHER": "new"}


class _FakeMCPRepo:
    def __init__(self, *, global_name_exists: bool = False, scoped_exists: bool = False) -> None:
        self.global_name_exists = global_name_exists
        self.scoped_exists = scoped_exists
        self.saved = None

    async def exists_global_name(self, name: str) -> bool:
        return self.global_name_exists

    async def get_by_name(self, name: str, scope=None):
        return object() if self.scoped_exists else None

    async def save(self, record, enc_url, url_enc, enc_headers, headers_enc, enc_env, env_enc):
        self.saved = record


class _FakeUoW:
    def __init__(self, repo: _FakeMCPRepo) -> None:
        self.mcp_server = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeCipher:
    pass


@pytest.mark.asyncio
async def test_create_server_rejects_private_name_colliding_with_global():
    repo = _FakeMCPRepo(global_name_exists=True)
    service = MCPServerService(lambda: _FakeUoW(repo), _FakeCipher())
    record = MCPServerRecord(
        id="1",
        name="shared",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://example.com/mcp",
        visibility=ResourceVisibility.PRIVATE,
    )
    scope = OwnerScope.personal("user-1")
    with pytest.raises(BadRequestError, match="全局 MCP 服务占用"):
        await service.create_server(record, scope=scope, is_admin=False)
