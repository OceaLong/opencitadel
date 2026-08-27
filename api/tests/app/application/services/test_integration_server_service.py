import pytest

from app.application.ports.crypto import OutboundNetworkPolicy
from app.application.services.integration_server_service import (
    A2AIntegrationService,
    MCPServerService,
    _apply_masked_secret_updates,
    _merge_url_secrets,
    _should_keep,
)
from app.domain.errors import BadRequestError, ForbiddenError
from app.domain.models.inference import ResourceVisibility
from app.domain.models.integration_runtime import MCPTransport
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.scope import OwnerScope
from app.domain.services.tools.capability_policy import INTEGRATION_READ
from app.infrastructure.adapters.security_ports import FernetSecretEnvelopeAdapter
from app.infrastructure.security.api_key_cipher import ApiKeyCipher

_OUTBOUND_POLICY = OutboundNetworkPolicy(
    allowed_ports=frozenset({80, 443, 8090, 8091}),
)


def _mcp_service(uow_factory) -> MCPServerService:
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    return MCPServerService(
        uow_factory=uow_factory,
        secret_envelope=FernetSecretEnvelopeAdapter(cipher),
        outbound_policy=_OUTBOUND_POLICY,
    )


def _a2a_service(uow_factory) -> A2AIntegrationService:
    return A2AIntegrationService(
        uow_factory=uow_factory,
        outbound_policy=_OUTBOUND_POLICY,
    )


def test_should_keep_empty_string():
    assert _should_keep("") is True
    assert _should_keep("   ") is True


def test_should_keep_masked_value():
    assert _should_keep("abcd****wxyz") is True


def test_should_keep_plain_new_value():
    assert _should_keep("new-key-value") is False


def test_merge_url_secrets_preserves_existing_when_masked():
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    updated = "https://mcp.amap.com/mcp?key=3244****2424"
    assert _merge_url_secrets(updated, existing) == existing


def test_merge_url_secrets_preserves_existing_when_empty():
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    assert _merge_url_secrets("", existing) == existing


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


def test_apply_masked_secret_updates_drops_removed_keys():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"OTHER": "new"}


@pytest.mark.asyncio
async def test_tenant_cannot_create_mcp_tool_policy_declarations():
    service = _mcp_service(lambda: None)
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
    service = _a2a_service(lambda: None)

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

    async def commit(self):
        return None

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
    service = _mcp_service(lambda: _MCPUow(repo))
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
async def test_update_server_accepts_bundled_collector_default_port():
    """F1 (task-2-report.md): update_server()'s outbound-URL revalidation must
    not unconditionally reject the bundled Ops Patrol Collector's port
    (docker-compose.yml deploys opencitadel-ops-collector on 8090) -- see
    core/config.py DeploymentSettings.outbound_allowed_ports' default, which now
    includes 8090/8091 for exactly this reason. This is the exact update the
    documented admin flow (docs/operations/ops-patrol.md "Register the MCP
    Server") and api/app/seed_demo.py both perform."""
    existing = MCPServerRecord(
        id="mcp-1",
        name="ops-collector",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="http://opencitadel-ops-collector:8090/mcp",
        visibility=ResourceVisibility.GLOBAL,
    )
    repo = _MCPRepo(existing)
    service = _mcp_service(lambda: _MCPUow(repo))
    update = MCPServerRecord(
        id="ignored",
        name="ops-collector",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="http://opencitadel-ops-collector:8090/mcp",
        visibility=ResourceVisibility.GLOBAL,
        enabled=True,
        tool_policies={"get_capabilities": INTEGRATION_READ},
    )

    result = await service.update_server("mcp-1", update, is_admin=True)

    assert result.enabled is True
    assert repo.saved is not None
    assert repo.saved.url == "http://opencitadel-ops-collector:8090/mcp"


@pytest.mark.asyncio
async def test_update_server_still_rejects_port_outside_allowlist():
    existing = MCPServerRecord(
        id="mcp-1",
        name="tickets",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test:9999/mcp",
        visibility=ResourceVisibility.GLOBAL,
    )
    repo = _MCPRepo(existing)
    service = _mcp_service(lambda: _MCPUow(repo))
    update = MCPServerRecord(
        id="ignored",
        name="tickets",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test:9999/mcp",
        visibility=ResourceVisibility.GLOBAL,
        enabled=True,
    )

    with pytest.raises(BadRequestError, match="端口未获批准"):
        await service.update_server("mcp-1", update, is_admin=True)


@pytest.mark.asyncio
async def test_tenant_update_explicitly_clearing_tool_policies_is_denied():
    repo = _MCPRepo(_private_mcp_with_admin_policy())
    service = _mcp_service(lambda: _MCPUow(repo))
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
    service = _mcp_service(lambda: _MCPUow(repo))
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


class _A2ARepo:
    def __init__(self, record: A2AServerRecord) -> None:
        self.record = record
        self.saved: A2AServerRecord | None = None

    async def get_by_id(self, server_id, scope=None):
        return self.record

    async def save(self, record):
        self.saved = record


class _A2AUow:
    def __init__(self, repo: _A2ARepo) -> None:
        self.a2a_server = repo

    async def __aenter__(self):
        return self

    async def commit(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_a2a_update_preserves_omitted_admin_policy_and_created_at() -> None:
    existing = A2AServerRecord(
        id="a2a-1",
        base_url="https://agent.example.test",
        visibility=ResourceVisibility.PRIVATE,
        owner_user_id="user-1",
        tool_policies={"get_remote_agent_cards": INTEGRATION_READ},
    )
    repo = _A2ARepo(existing)
    service = _a2a_service(lambda: _A2AUow(repo))
    updates = A2AServerRecord(
        id="ignored",
        base_url="https://agent.example.test/v2",
        visibility=ResourceVisibility.PRIVATE,
    )

    result = await service.update_server(
        "a2a-1",
        updates,
        scope=OwnerScope.personal("user-1"),
        is_admin=False,
    )

    assert result.id == "a2a-1"
    assert result.created_at == existing.created_at
    assert result.tool_policies == {"get_remote_agent_cards": INTEGRATION_READ}


@pytest.mark.asyncio
async def test_a2a_tenant_cannot_clear_admin_policy() -> None:
    existing = A2AServerRecord(
        id="a2a-1",
        base_url="https://agent.example.test",
        visibility=ResourceVisibility.PRIVATE,
        owner_user_id="user-1",
        tool_policies={"get_remote_agent_cards": INTEGRATION_READ},
    )
    repo = _A2ARepo(existing)
    service = _a2a_service(lambda: _A2AUow(repo))
    updates = A2AServerRecord(
        id="ignored",
        base_url="https://agent.example.test/v2",
        visibility=ResourceVisibility.PRIVATE,
        tool_policies={},
    )

    with pytest.raises(ForbiddenError):
        await service.update_server(
            "a2a-1",
            updates,
            scope=OwnerScope.personal("user-1"),
            is_admin=False,
        )
