#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import ValidationError

from app.application.errors.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.application.services.audit_service import AuditService
from app.domain.models.app_config import A2AConfig, MCPConfig, MCPTransport
from app.domain.models.audit_log import AuditLog
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.llm_model import ResourceVisibility
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.integration_config_builder import a2a_records_to_config, mcp_records_to_config
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.secret_dict_cipher import encrypt_secret_dict, encrypt_url

logger = logging.getLogger(__name__)


def _ensure_stdio_allowed(record: MCPServerRecord, *, is_admin: bool) -> None:
    if not is_admin and record.transport == MCPTransport.STDIO:
        raise ForbiddenError("仅管理员可配置 stdio 类型的 MCP 服务")


def _ensure_valid_mcp_record(record: MCPServerRecord) -> None:
    try:
        MCPServerRecord.model_validate(record.model_dump())
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        raise BadRequestError(message) from exc


def _should_keep(new_val: Any) -> bool:
    if not isinstance(new_val, str):
        return False
    if not new_val.strip():
        return True
    if "****" in new_val:
        return True
    return ApiKeyCipher.looks_like_fernet_token(new_val)


def _apply_masked_secret_updates(
    updates: dict,
    existing: dict,
) -> dict:
    merged: Dict[str, Any] = {}
    for key, value in updates.items():
        if _should_keep(value):
            if key in existing:
                merged[key] = existing[key]
        else:
            merged[key] = value
    return merged


def _merge_url_secrets(updated_url: Optional[str], existing_url: Optional[str]) -> Optional[str]:
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
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(merged_pairs), parsed.fragment)
    )


class MCPServerService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        cipher: ApiKeyCipher,
        audit_service: Optional[AuditService] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._cipher = cipher
        self._audit_service = audit_service

    async def list_servers(self, mask: bool = True, scope: Optional[OwnerScope] = None) -> List[MCPServerRecord]:
        async with self._uow_factory() as uow:
            records = await uow.mcp_server.list_all(scope=scope)
        return [r.mask_secrets() if mask else r for r in records]

    async def resolve_mcp_config(self, scope: Optional[OwnerScope] = None) -> MCPConfig:
        async with self._uow_factory() as uow:
            records = await uow.mcp_server.list_all(scope=scope)
        return mcp_records_to_config(records)

    async def create_server(
        self,
        record: MCPServerRecord,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
        *,
        is_admin: bool = False,
    ) -> MCPServerRecord:
        _ensure_stdio_allowed(record, is_admin=is_admin)
        _ensure_valid_mcp_record(record)
        if scope is not None and record.visibility != ResourceVisibility.GLOBAL:
            record.owner_user_id = scope.user_id
        enc_headers, headers_enc = encrypt_secret_dict(record.headers, self._cipher)
        enc_env, env_enc = encrypt_secret_dict(record.env, self._cipher)
        enc_url, url_enc = encrypt_url(record.url, self._cipher)
        async with self._uow_factory() as uow:
            if record.visibility != ResourceVisibility.GLOBAL:
                if await uow.mcp_server.exists_global_name(record.name):
                    raise BadRequestError("该名称已被全局 MCP 服务占用，请更换名称")
            existing = await uow.mcp_server.get_by_name(record.name, scope=scope)
            if existing:
                raise BadRequestError(f"MCP 服务[{record.name}]已存在")
            await uow.mcp_server.save(record, enc_url, url_enc, enc_headers, headers_enc, enc_env, env_enc)
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
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
        *,
        is_admin: bool = False,
    ) -> MCPServerRecord:
        _ensure_stdio_allowed(updates, is_admin=is_admin)
        async with self._uow_factory() as uow:
            existing = await uow.mcp_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"MCP 服务[{server_id}]不存在")
            updates.id = server_id
            updates.url = _merge_url_secrets(updates.url, existing.url)
            if updates.headers is not None:
                updates.headers = _apply_masked_secret_updates(updates.headers, existing.headers or {})
            if updates.env is not None:
                updates.env = _apply_masked_secret_updates(updates.env, existing.env or {})
            _ensure_valid_mcp_record(updates)
            enc_headers, headers_enc = encrypt_secret_dict(updates.headers, self._cipher)
            enc_env, env_enc = encrypt_secret_dict(updates.env, self._cipher)
            enc_url, url_enc = encrypt_url(updates.url, self._cipher)
            await uow.mcp_server.save(updates, enc_url, url_enc, enc_headers, headers_enc, enc_env, env_enc)
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
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
    ) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.mcp_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"MCP 服务[{server_id}]不存在")
            await uow.mcp_server.delete_by_id(server_id)
        await self._audit(actor_user_id, "mcp_server.delete", server_id, {})

    async def set_enabled(
        self,
        server_id: str,
        enabled: bool,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
    ) -> MCPServerRecord:
        async with self._uow_factory() as uow:
            existing = await uow.mcp_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"MCP 服务[{server_id}]不存在")
            existing.enabled = enabled
            enc_headers, headers_enc = encrypt_secret_dict(existing.headers, self._cipher)
            enc_env, env_enc = encrypt_secret_dict(existing.env, self._cipher)
            enc_url, url_enc = encrypt_url(existing.url, self._cipher)
            await uow.mcp_server.save(existing, enc_url, url_enc, enc_headers, headers_enc, enc_env, env_enc)
        await self._audit(actor_user_id, "mcp_server.set_enabled", server_id, {"enabled": enabled})
        return existing.mask_secrets()

    async def _audit(self, actor_user_id: Optional[str], action: str, resource_id: str, metadata: dict) -> None:
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


class A2AServerConfigService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: Optional[AuditService] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service

    async def list_servers(self, scope: Optional[OwnerScope] = None) -> List[A2AServerRecord]:
        async with self._uow_factory() as uow:
            return await uow.a2a_server.list_all(scope=scope)

    async def resolve_a2a_config(self, scope: Optional[OwnerScope] = None) -> A2AConfig:
        async with self._uow_factory() as uow:
            records = await uow.a2a_server.list_all(scope=scope)
        return a2a_records_to_config(records)

    async def create_server(
        self,
        base_url: str,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
        visibility: ResourceVisibility = ResourceVisibility.GLOBAL,
    ) -> A2AServerRecord:
        record = A2AServerRecord(
            id=str(uuid.uuid4()),
            base_url=base_url,
            enabled=True,
            visibility=visibility,
            owner_user_id=scope.user_id if scope and visibility != ResourceVisibility.GLOBAL else None,
        )
        async with self._uow_factory() as uow:
            await uow.a2a_server.save(record)
        await self._audit(actor_user_id, "a2a_server.create", record.id, record.model_dump(mode="json"))
        return record

    async def delete_server(
        self,
        server_id: str,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
    ) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.a2a_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"A2A 服务[{server_id}]不存在")
            await uow.a2a_server.delete_by_id(server_id)
        await self._audit(actor_user_id, "a2a_server.delete", server_id, {})

    async def set_enabled(
        self,
        server_id: str,
        enabled: bool,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
    ) -> A2AServerRecord:
        async with self._uow_factory() as uow:
            existing = await uow.a2a_server.get_by_id(server_id, scope=scope)
            if not existing:
                raise NotFoundError(f"A2A 服务[{server_id}]不存在")
            existing.enabled = enabled
            await uow.a2a_server.save(existing)
        await self._audit(actor_user_id, "a2a_server.set_enabled", server_id, {"enabled": enabled})
        return existing

    async def _audit(self, actor_user_id: Optional[str], action: str, resource_id: str, metadata: dict) -> None:
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
