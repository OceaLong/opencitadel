#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.services.integration_server_service import (
    A2AServerConfigService,
    MCPServerService,
    _apply_masked_secret_updates,
    _merge_url_secrets,
    _should_keep,
)
from app.application.errors.exceptions import ForbiddenError
from app.domain.models.app_config import MCPTransport
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.llm_model import ResourceVisibility
from app.domain.models.scope import OwnerScope
from app.domain.services.tools.capability_policy import INTEGRATION_READ
import pytest
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


def test_should_keep_empty_string():
    assert _should_keep("") is True
    assert _should_keep("   ") is True


def test_should_keep_masked_value():
    assert _should_keep("abcd****wxyz") is True


def test_should_keep_fernet_token():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    token = cipher.encrypt("secret-value")
    assert ApiKeyCipher.looks_like_fernet_token(token) is True
    assert _should_keep(token) is True


def test_should_keep_plain_new_value():
    assert _should_keep("new-key-value") is False


def test_merge_url_secrets_preserves_existing_when_masked():
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    updated = "https://mcp.amap.com/mcp?key=3244****2424"
    assert _merge_url_secrets(updated, existing) == existing


def test_merge_url_secrets_preserves_existing_when_empty():
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    assert _merge_url_secrets("", existing) == existing


def test_merge_url_secrets_preserves_existing_when_fernet_token():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    token = cipher.encrypt(existing)
    assert _merge_url_secrets(token, existing) == existing


def test_merge_url_secrets_uses_new_value_when_not_masked():
    existing = "https://mcp.amap.com/mcp?key=old-key-value"
    updated = "https://mcp.amap.com/mcp?key=new-key-value"
    assert _merge_url_secrets(updated, existing) == updated


def test_merge_url_secrets_merges_blank_param_with_existing():
    existing = "https://mcp.amap.com/mcp?key=secret-key&foo=bar"
    updated = "https://mcp.amap.com/mcp?key="
    assert _merge_url_secrets(updated, existing) == "https://mcp.amap.com/mcp?key=secret-key"


def test_merge_url_secrets_updates_service_url_but_keeps_blank_secret():
    existing = "https://old.example.com/mcp?key=secret-key"
    updated = "https://new.example.com/mcp?key="
    assert _merge_url_secrets(updated, existing) == "https://new.example.com/mcp?key=secret-key"


def test_merge_url_secrets_drops_removed_params():
    existing = "https://mcp.amap.com/mcp?key=secret-key&foo=bar"
    updated = "https://mcp.amap.com/mcp?key="
    assert "foo" not in (_merge_url_secrets(updated, existing) or "")


def test_merge_url_secrets_no_query_returns_updated_url():
    existing = "https://mcp.amap.com/mcp?key=secret"
    updated = "https://other.example.com/mcp"
    assert _merge_url_secrets(updated, existing) == updated


def test_apply_masked_secret_updates_per_key_with_masked():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"API_KEY": "****", "OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"API_KEY": "secret", "OTHER": "new"}


def test_apply_masked_secret_updates_blank_value_keeps_existing():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"API_KEY": "", "OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"API_KEY": "secret", "OTHER": "new"}


def test_apply_masked_secret_updates_fernet_token_keeps_existing():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    existing = {"API_KEY": "secret"}
    updates = {"API_KEY": cipher.encrypt("secret")}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"API_KEY": "secret"}


def test_apply_masked_secret_updates_drops_removed_keys():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"OTHER": "new"}


@pytest.mark.asyncio
async def test_tenant_cannot_create_mcp_tool_policy_declarations():
    service = MCPServerService(
        uow_factory=lambda: None,
        cipher=ApiKeyCipher("test-secret-key-for-unit-tests-only"),
    )
    record = MCPServerRecord(
        id="mcp-1",
        name="tickets",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test",
        visibility=ResourceVisibility.PRIVATE,
        tool_policies={"lookup_ticket": INTEGRATION_READ},
    )

    with pytest.raises(ForbiddenError):
        await service.create_server(record, is_admin=False)


@pytest.mark.asyncio
async def test_tenant_cannot_create_a2a_tool_policy_declarations():
    service = A2AServerConfigService(uow_factory=lambda: None)

    with pytest.raises(ForbiddenError):
        await service.create_server(
            "https://agent.example.test",
            tool_policies={"get_remote_agent_cards": INTEGRATION_READ},
            visibility=ResourceVisibility.PRIVATE,
            is_admin=False,
        )


class _MCPRepo:
    def __init__(self, record):
        self.record = record
        self.saved = None

    async def get_by_id(self, server_id, scope=None):
        return self.record

    async def save(self, record, *args):
        self.saved = record


class _MCPUow:
    def __init__(self, repo):
        self.mcp_server = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _private_mcp_with_admin_policy():
    return MCPServerRecord(
        id="mcp-1",
        name="tickets",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test",
        visibility=ResourceVisibility.PRIVATE,
        owner_user_id="user-1",
        tool_policies={"lookup_ticket": INTEGRATION_READ},
    )


@pytest.mark.asyncio
async def test_tenant_update_omitting_tool_policies_preserves_admin_declarations():
    repo = _MCPRepo(_private_mcp_with_admin_policy())
    service = MCPServerService(
        uow_factory=lambda: _MCPUow(repo),
        cipher=ApiKeyCipher("test-secret-key-for-unit-tests-only"),
    )
    update = MCPServerRecord(
        id="ignored",
        name="tickets-renamed",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test",
        visibility=ResourceVisibility.PRIVATE,
        owner_user_id="user-1",
    )

    result = await service.update_server(
        "mcp-1",
        update,
        scope=OwnerScope.personal("user-1"),
        is_admin=False,
    )

    assert result.tool_policies == {"lookup_ticket": INTEGRATION_READ}
    assert repo.saved.tool_policies == {"lookup_ticket": INTEGRATION_READ}


@pytest.mark.asyncio
async def test_tenant_update_explicitly_clearing_tool_policies_is_denied():
    repo = _MCPRepo(_private_mcp_with_admin_policy())
    service = MCPServerService(
        uow_factory=lambda: _MCPUow(repo),
        cipher=ApiKeyCipher("test-secret-key-for-unit-tests-only"),
    )
    update = MCPServerRecord(
        id="ignored",
        name="tickets",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test",
        visibility=ResourceVisibility.PRIVATE,
        owner_user_id="user-1",
        tool_policies={},
    )

    with pytest.raises(ForbiddenError):
        await service.update_server(
            "mcp-1",
            update,
            scope=OwnerScope.personal("user-1"),
            is_admin=False,
        )


@pytest.mark.asyncio
async def test_tenant_update_round_trips_unchanged_tool_policies():
    repo = _MCPRepo(_private_mcp_with_admin_policy())
    service = MCPServerService(
        uow_factory=lambda: _MCPUow(repo),
        cipher=ApiKeyCipher("test-secret-key-for-unit-tests-only"),
    )
    update = MCPServerRecord(
        id="ignored",
        name="tickets",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test",
        visibility=ResourceVisibility.PRIVATE,
        owner_user_id="user-1",
        tool_policies={"lookup_ticket": INTEGRATION_READ},
    )

    result = await service.update_server(
        "mcp-1",
        update,
        scope=OwnerScope.personal("user-1"),
        is_admin=False,
    )

    assert result.tool_policies == {"lookup_ticket": INTEGRATION_READ}
