import logging
import uuid
from collections.abc import Callable, Collection
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import ValidationError

from app.application.ports.crypto import OutboundNetworkPolicy, SecretEnvelopePort
from app.application.services.audit_service import AuditService
from app.domain.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.models.audit_log import AuditLog
from app.domain.models.inference import ResourceVisibility
from app.domain.models.integration_runtime import A2ARuntime, MCPRuntime, MCPTransport
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.tool_policy import ToolExecutionPolicy
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.integration_runtime_builder import (
    a2a_records_to_runtime,
    mcp_records_to_runtime,
)
from app.domain.utils.mcp_url import validate_mcp_http_url
from app.domain.utils.outbound_url import (
    OutboundURLRejected,
    resolve_outbound_url,
)

logger = logging.getLogger(__name__)


def _ensure_stdio_allowed(record: MCPServerRecord, *, is_admin: bool) -> None:
    if not is_admin and record.transport == MCPTransport.STDIO:
        raise ForbiddenError("仅管理员可配置 stdio 类型的 MCP 服务")


def _ensure_valid_mcp_record(
    record: MCPServerRecord,
    outbound_policy: OutboundNetworkPolicy,
) -> None:
    try:
        MCPServerRecord.model_validate(record.model_dump())
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        raise BadRequestError(message) from exc
    if record.transport in (MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP) and record.url:
        try:
            validate_mcp_http_url(
                record.url,
                resolve_dns=False,
                allowed_ports=set(outbound_policy.allowed_ports),
                allow_private_hosts=set(outbound_policy.allow_private_hosts),
            )
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc


def _should_keep(new_val: Any) -> bool:
    if not isinstance(new_val, str):
        return False
    if not new_val.strip():
        return True
    return "****" in new_val


def _apply_masked_secret_updates(
    updates: dict,
    existing: dict,
) -> dict:
    merged: dict[str, Any] = {}
    for key, value in updates.items():
        if _should_keep(value):
            if key in existing:
                merged[key] = existing[key]
        else:
            merged[key] = value
    return merged


def _merge_url_secrets(updated_url: str | None, existing_url: str | None) -> str | None:
    if updated_url is None:
        return existing_url
    if _should_keep(updated_url):
        return existing_url
    parsed = urlparse(updated_url)
    if not parsed.query:
        return updated_url
    old_params = dict(parse_qsl(urlparse(existing_url or "").query, keep_blank_values=True))
    merged_pairs = [
        (key, old_params.get(key, value) if _should_keep(value) else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(merged_pairs),
            parsed.fragment,
        )
    )


def _bind_ownership(record, scope: OwnerScope | None) -> None:
    if record.visibility == ResourceVisibility.GLOBAL:
        record.owner_user_id = None
        record.team_id = None
        return
    if scope is None:
        raise BadRequestError("私有集成服务必须绑定访问作用域")
    record.owner_user_id = scope.user_id
    record.team_id = scope.team_id if scope.type == OwnerScopeType.TEAM else None


class MCPServerService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        secret_envelope: SecretEnvelopePort,
        outbound_policy: OutboundNetworkPolicy,
        audit_service: AuditService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._secret_envelope = secret_envelope
        self._outbound_policy = outbound_policy
        self._audit_service = audit_service

    async def list_servers(
        self, mask: bool = True, scope: OwnerScope | None = None
    ) -> list[MCPServerRecord]:
        async with self._uow_factory() as uow:
            records = await uow.mcp_server.list_all(scope=scope)
        return [r.mask_secrets() if mask else r for r in records]

    async def resolve_mcp_runtime(
        self,
        scope: OwnerScope | None = None,
        *,
        server_refs: Collection[str] | None = None,
    ) -> MCPRuntime:
        async with self._uow_factory() as uow:
            records = await uow.mcp_server.list_all(scope=scope)
        if server_refs is not None:
            allowed = frozenset(server_refs)
            records = [record for record in records if record.id in allowed]
        return mcp_records_to_runtime(records)

    async def create_server(
        self,
        record: MCPServerRecord,
        scope: OwnerScope | None = None,
        actor_user_id: str | None = None,
        *,
        is_admin: bool = False,
    ) -> MCPServerRecord:
        _ensure_stdio_allowed(record, is_admin=is_admin)
        if record.tool_policies and not is_admin:
            raise ForbiddenError("仅管理员可声明 MCP 工具能力策略")
        if record.visibility == ResourceVisibility.GLOBAL and not is_admin:
            raise ForbiddenError("只有管理员可创建全局 MCP 服务")
        _ensure_valid_mcp_record(record, self._outbound_policy)
        _bind_ownership(record, scope)
        headers = self._secret_envelope.encrypt_mapping(record.headers)
        env = self._secret_envelope.encrypt_mapping(record.env)
        url = self._secret_envelope.encrypt_url(record.url)
        async with self._uow_factory() as uow:
            if (
                record.visibility != ResourceVisibility.GLOBAL
                and await uow.mcp_server.exists_global_name(record.name)
            ):
                raise BadRequestError("该名称已被全局 MCP 服务占用，请更换名称")
            existing = await uow.mcp_server.get_by_name(record.name, scope=scope)
            if existing:
                raise BadRequestError(f"MCP 服务[{record.name}]已存在")
            await uow.mcp_server.save(
                record,
                url.value,
                url.scheme,
                headers.value,
                headers.scheme,
                env.value,
                env.scheme,
            )
            await uow.commit()
        await self._audit(
            actor_user_id,
            "mcp_server.create",
            record.id,
            record.mask_secrets().model_dump(mode="json"),
        )
        return record.mask_secrets()

    async def update_server(
        self,
        server_id: str,
        updates: MCPServerRecord,
        scope: OwnerScope | None = None,
        actor_user_id: str | None = None,
        *,
        is_admin: bool = False,
    ) -> MCPServerRecord:
        _ensure_stdio_allowed(updates, is_admin=is_admin)
        async with self._uow_factory() as uow:
            existing = await uow.mcp_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"MCP 服务[{server_id}]不存在")
            if updates.visibility == ResourceVisibility.GLOBAL and not is_admin:
                raise ForbiddenError("只有管理员可修改全局 MCP 服务")
            if existing.visibility != updates.visibility:
                raise BadRequestError("MCP 服务可见性不可通过更新修改")
            if existing.visibility == ResourceVisibility.GLOBAL and not is_admin:
                raise ForbiddenError("只有管理员可修改全局 MCP 服务")
            policies_supplied = "tool_policies" in updates.model_fields_set
            if (
                not is_admin
                and policies_supplied
                and updates.tool_policies != existing.tool_policies
            ):
                raise ForbiddenError("仅管理员可修改 MCP 工具能力策略")
            if not policies_supplied:
                updates.tool_policies = existing.tool_policies
            updates.id = server_id
            updates.created_at = existing.created_at
            updates.transport_options = dict(updates.transport_options)
            updates.url = _merge_url_secrets(updates.url, existing.url)
            if updates.headers is not None:
                updates.headers = _apply_masked_secret_updates(
                    updates.headers, existing.headers or {}
                )
            if updates.env is not None:
                updates.env = _apply_masked_secret_updates(updates.env, existing.env or {})
            _bind_ownership(updates, scope)
            _ensure_valid_mcp_record(updates, self._outbound_policy)
            headers = self._secret_envelope.encrypt_mapping(updates.headers)
            env = self._secret_envelope.encrypt_mapping(updates.env)
            url = self._secret_envelope.encrypt_url(updates.url)
            await uow.mcp_server.save(
                updates,
                url.value,
                url.scheme,
                headers.value,
                headers.scheme,
                env.value,
                env.scheme,
            )
            await uow.commit()
        await self._audit(
            actor_user_id,
            "mcp_server.update",
            server_id,
            updates.mask_secrets().model_dump(mode="json"),
        )
        return updates.mask_secrets()

    async def delete_server(
        self,
        server_id: str,
        scope: OwnerScope | None = None,
        actor_user_id: str | None = None,
        *,
        is_admin: bool = False,
    ) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.mcp_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"MCP 服务[{server_id}]不存在")
            if existing.visibility == ResourceVisibility.GLOBAL and not is_admin:
                raise ForbiddenError("只有管理员可删除全局 MCP 服务")
            await uow.mcp_server.delete_by_id(server_id)
            await uow.commit()
        await self._audit(actor_user_id, "mcp_server.delete", server_id, {})

    async def set_enabled(
        self,
        server_id: str,
        enabled: bool,
        scope: OwnerScope | None = None,
        actor_user_id: str | None = None,
        *,
        is_admin: bool = False,
    ) -> MCPServerRecord:
        async with self._uow_factory() as uow:
            existing = await uow.mcp_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"MCP 服务[{server_id}]不存在")
            if existing.visibility == ResourceVisibility.GLOBAL and not is_admin:
                raise ForbiddenError("只有管理员可修改全局 MCP 服务")
            existing.enabled = enabled
            headers = self._secret_envelope.encrypt_mapping(existing.headers)
            env = self._secret_envelope.encrypt_mapping(existing.env)
            url = self._secret_envelope.encrypt_url(existing.url)
            await uow.mcp_server.save(
                existing,
                url.value,
                url.scheme,
                headers.value,
                headers.scheme,
                env.value,
                env.scheme,
            )
            await uow.commit()
        await self._audit(actor_user_id, "mcp_server.set_enabled", server_id, {"enabled": enabled})
        return existing.mask_secrets()

    async def _audit(
        self, actor_user_id: str | None, action: str, resource_id: str, metadata: dict
    ) -> None:
        if self._audit_service is None:
            return
        await self._audit_service.record(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                resource_type="mcp_server",
                resource_id=resource_id,
                metadata=metadata,
            )
        )


class A2AIntegrationService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        outbound_policy: OutboundNetworkPolicy,
        audit_service: AuditService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._outbound_policy = outbound_policy
        self._audit_service = audit_service

    async def list_servers(self, scope: OwnerScope | None = None) -> list[A2AServerRecord]:
        async with self._uow_factory() as uow:
            return await uow.a2a_server.list_all(scope=scope)

    async def resolve_a2a_runtime(
        self,
        scope: OwnerScope | None = None,
        *,
        server_refs: Collection[str] | None = None,
    ) -> A2ARuntime:
        async with self._uow_factory() as uow:
            records = await uow.a2a_server.list_all(scope=scope)
        if server_refs is not None:
            allowed = frozenset(server_refs)
            records = [record for record in records if record.id in allowed]
        return a2a_records_to_runtime(records)

    async def create_server(
        self,
        base_url: str,
        scope: OwnerScope | None = None,
        actor_user_id: str | None = None,
        visibility: ResourceVisibility = ResourceVisibility.GLOBAL,
        enabled: bool = True,
        tool_policies: dict[str, ToolExecutionPolicy] | None = None,
        *,
        is_admin: bool = False,
    ) -> A2AServerRecord:
        if tool_policies and not is_admin:
            raise ForbiddenError("仅管理员可声明 A2A 工具能力策略")
        if visibility == ResourceVisibility.GLOBAL and not is_admin:
            raise ForbiddenError("只有管理员可创建全局 A2A 服务")
        try:
            resolved = resolve_outbound_url(
                base_url,
                allowed_ports=set(self._outbound_policy.allowed_ports),
                allow_private_hosts=set(self._outbound_policy.allow_private_hosts),
                resolve_dns=False,
            )
        except OutboundURLRejected as exc:
            raise BadRequestError(str(exc)) from exc
        record = A2AServerRecord(
            id=str(uuid.uuid4()),
            base_url=resolved.url,
            enabled=enabled,
            tool_policies=tool_policies or {},
            visibility=visibility,
        )
        _bind_ownership(record, scope)
        async with self._uow_factory() as uow:
            await uow.a2a_server.save(record)
            await uow.commit()
        await self._audit(
            actor_user_id, "a2a_server.create", record.id, record.model_dump(mode="json")
        )
        return record

    async def update_server(
        self,
        server_id: str,
        updates: A2AServerRecord,
        scope: OwnerScope | None = None,
        actor_user_id: str | None = None,
        *,
        is_admin: bool = False,
    ) -> A2AServerRecord:
        async with self._uow_factory() as uow:
            existing = await uow.a2a_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"A2A 服务[{server_id}]不存在")
            if existing.visibility != updates.visibility:
                raise BadRequestError("A2A 服务可见性不可通过更新修改")
            if existing.visibility == ResourceVisibility.GLOBAL and not is_admin:
                raise ForbiddenError("只有管理员可修改全局 A2A 服务")
            policies_supplied = "tool_policies" in updates.model_fields_set
            if (
                not is_admin
                and policies_supplied
                and updates.tool_policies != existing.tool_policies
            ):
                raise ForbiddenError("仅管理员可修改 A2A 工具能力策略")
            if not policies_supplied:
                updates.tool_policies = existing.tool_policies
            try:
                resolved = resolve_outbound_url(
                    updates.base_url,
                    allowed_ports=set(self._outbound_policy.allowed_ports),
                    allow_private_hosts=set(self._outbound_policy.allow_private_hosts),
                    resolve_dns=False,
                )
            except OutboundURLRejected as exc:
                raise BadRequestError(str(exc)) from exc
            updates.id = server_id
            updates.created_at = existing.created_at
            updates.base_url = resolved.url
            _bind_ownership(updates, scope)
            await uow.a2a_server.save(updates)
            await uow.commit()
        await self._audit(
            actor_user_id,
            "a2a_server.update",
            server_id,
            updates.model_dump(mode="json"),
        )
        return updates

    async def delete_server(
        self,
        server_id: str,
        scope: OwnerScope | None = None,
        actor_user_id: str | None = None,
        *,
        is_admin: bool = False,
    ) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.a2a_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"A2A 服务[{server_id}]不存在")
            if existing.visibility == ResourceVisibility.GLOBAL and not is_admin:
                raise ForbiddenError("只有管理员可删除全局 A2A 服务")
            await uow.a2a_server.delete_by_id(server_id)
            await uow.commit()
        await self._audit(actor_user_id, "a2a_server.delete", server_id, {})

    async def set_enabled(
        self,
        server_id: str,
        enabled: bool,
        scope: OwnerScope | None = None,
        actor_user_id: str | None = None,
        *,
        is_admin: bool = False,
    ) -> A2AServerRecord:
        async with self._uow_factory() as uow:
            existing = await uow.a2a_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"A2A 服务[{server_id}]不存在")
            if existing.visibility == ResourceVisibility.GLOBAL and not is_admin:
                raise ForbiddenError("只有管理员可修改全局 A2A 服务")
            existing.enabled = enabled
            await uow.a2a_server.save(existing)
            await uow.commit()
        await self._audit(actor_user_id, "a2a_server.set_enabled", server_id, {"enabled": enabled})
        return existing

    async def _audit(
        self, actor_user_id: str | None, action: str, resource_id: str, metadata: dict
    ) -> None:
        if self._audit_service is None:
            return
        await self._audit_service.record(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                resource_type="a2a_server",
                resource_id=resource_id,
                metadata=metadata,
            )
        )
