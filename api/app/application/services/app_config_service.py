#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import uuid
import asyncio
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from app.application.errors.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.application.services.config_provider import invalidate_runtime_config
from app.application.services.integration_server_service import A2AServerConfigService, MCPServerService
from app.application.services.owner_config_resolver import (
    merge_configs,
    validate_global_section,
    validate_user_override_payload,
)
from app.domain.models.app_config import (
    AppConfig,
    AgentConfig,
    MCPConfig,
    MCPTransport,
    A2AConfig,
)
from app.domain.models.app_config_revision import AppConfigRevision
from app.domain.models.app_config_scope import (
    ALL_APP_CONFIG_SECTIONS,
    GLOBAL_ONLY_SECTIONS,
    USER_OVERRIDABLE_SECTIONS,
    user_config_id,
)
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.scope import OwnerScope
from app.domain.repositories.app_config_repository import AppConfigRepository
from app.infrastructure.external.tools.connection_pool import A2AConnectionPool, MCPConnectionPool
from app.interfaces.schemas.app_config import ListMCPServerItem, ListA2AServerItem

logger = logging.getLogger(__name__)

_SECTION_MODELS: Dict[str, Type[BaseModel]] = {
    name: AppConfig.model_fields[name].annotation
    for name in ALL_APP_CONFIG_SECTIONS
}


class AppConfigService:
    """应用配置服务"""

    def __init__(
        self,
        app_config_repository: AppConfigRepository,
        mcp_server_service: Optional[MCPServerService] = None,
        a2a_server_service: Optional[A2AServerConfigService] = None,
    ) -> None:
        self.app_config_repository = app_config_repository
        self._mcp_server_service = mcp_server_service
        self._a2a_server_service = a2a_server_service

    async def _load_global_config(self) -> AppConfig:
        config = await self.app_config_repository.load_global()
        return config or AppConfig()

    async def _save_global_config(
        self,
        app_config: AppConfig,
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        await self.app_config_repository.save_global(app_config, changed_by=changed_by, note=note)
        invalidate_runtime_config()
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()

    async def resolve_for_owner(self, scope: Optional[OwnerScope] = None) -> AppConfig:
        global_config = await self._load_global_config()
        if scope is None:
            return global_config
        override_payload = await self.app_config_repository.load_user_override_payload(scope.user_id)
        return merge_configs(global_config, override_payload)

    async def get_section(
        self,
        section: str,
        *,
        scope: Optional[OwnerScope] = None,
        use_user_override: bool = False,
    ) -> BaseModel:
        validate_global_section(section)
        if use_user_override and scope is not None and section in USER_OVERRIDABLE_SECTIONS:
            override_payload = await self.app_config_repository.load_user_override_payload(scope.user_id)
            if section in override_payload:
                section_type = type(getattr(AppConfig(), section))
                return section_type.model_validate(override_payload[section])
        config = await self.resolve_for_owner(scope)
        return getattr(config, section)

    async def update_section(
        self,
        section: str,
        payload: dict,
        *,
        changed_by: Optional[str] = None,
        scope: Optional[OwnerScope] = None,
        is_admin: bool = False,
    ) -> BaseModel:
        validate_global_section(section)
        section_model = _SECTION_MODELS[section].model_validate(payload)

        if section in GLOBAL_ONLY_SECTIONS:
            if not is_admin:
                raise BadRequestError("仅管理员可修改全局配置段")
            app_config = await self._load_global_config()
            setattr(app_config, section, section_model)
            await self._save_global_config(app_config, changed_by=changed_by, note=f"update:{section}")
            return section_model

        if scope is None:
            raise BadRequestError("用户级配置段需要登录上下文")
        if is_admin:
            app_config = await self._load_global_config()
            setattr(app_config, section, section_model)
            await self._save_global_config(app_config, changed_by=changed_by, note=f"update:{section}")
            return section_model
        existing_payload = await self.app_config_repository.load_user_override_payload(scope.user_id)
        existing_payload[section] = section_model.model_dump(mode="json")
        validate_user_override_payload(existing_payload)
        await self.app_config_repository.save_user_override_payload(
            scope.user_id,
            existing_payload,
            changed_by=changed_by,
            note=f"update:{section}",
        )
        invalidate_runtime_config()
        self._notify_config_invalidate()
        return section_model

    async def delete_user_override(self, user_id: str, *, changed_by: Optional[str] = None) -> None:
        await self.app_config_repository.delete_user_override(user_id)
        invalidate_runtime_config()
        self._notify_config_invalidate()

    async def list_revisions(
        self,
        *,
        scope: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AppConfigRevision]:
        config_id = user_config_id(owner_user_id) if owner_user_id else "global"
        return await self.app_config_repository.list_revisions(
            config_id=config_id if scope else None,
            scope=scope,
            owner_user_id=owner_user_id,
            limit=limit,
            offset=offset,
        )

    async def rollback_revision(
        self,
        revision_id: str,
        *,
        changed_by: Optional[str] = None,
    ) -> AppConfig:
        restored = await self.app_config_repository.rollback_to_revision(
            revision_id,
            changed_by=changed_by,
        )
        invalidate_runtime_config()
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()
        return restored

    async def get_agent_config(self, scope: Optional[OwnerScope] = None) -> AgentConfig:
        return await self.get_section("agent_config", scope=scope, use_user_override=False)  # type: ignore[return-value]

    async def update_agent_config(
        self,
        agent_config: AgentConfig,
        *,
        changed_by: Optional[str] = None,
        scope: Optional[OwnerScope] = None,
        is_admin: bool = True,
    ) -> AgentConfig:
        return await self.update_section(  # type: ignore[return-value]
            "agent_config",
            agent_config.model_dump(mode="json"),
            changed_by=changed_by,
            scope=scope,
            is_admin=is_admin,
        )

    @staticmethod
    def _notify_config_invalidate() -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        from app.infrastructure.external.app_config_notifier import publish_config_invalidate
        loop.create_task(publish_config_invalidate())

    @staticmethod
    def _invalidate_runtime_pools() -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(MCPConnectionPool.invalidate_all())
        loop.create_task(A2AConnectionPool.invalidate_all())

    async def get_mcp_servers(self, scope: Optional[OwnerScope] = None) -> List[ListMCPServerItem]:
        if self._mcp_server_service is None:
            raise BadRequestError("MCP 服务未启用", error_key="errors.mcpNotEnabled")
        records = await self._mcp_server_service.list_servers(scope=scope)
        mcp_config = await self._mcp_server_service.resolve_mcp_config(scope)
        manager = MCPConnectionPool.try_get_cached(mcp_config)
        self._refresh_mcp_pool_background(mcp_config)

        tools: Dict[str, List[Any]] = manager.tools if manager else {}
        connection_errors = manager.connection_errors if manager else {}

        items: List[ListMCPServerItem] = []
        for record in records:
            if not record.enabled:
                status = "disabled"
                error = None
            elif record.name in connection_errors:
                status = "error"
                error = connection_errors[record.name]
            elif record.name in tools:
                status = "connected"
                error = None
            elif manager is None:
                status = "pending"
                error = None
            else:
                status = "error"
                error = connection_errors.get(record.name, "连接失败")
            items.append(
                ListMCPServerItem(
                    server_name=record.name,
                    server_id=record.id,
                    enabled=record.enabled,
                    transport=record.transport,
                    tools=[tool.name for tool in tools.get(record.name, [])],
                    connection_status=status,
                    connection_error=error,
                    config={
                        "transport": record.transport.value,
                        "enabled": record.enabled,
                        "description": record.description,
                        "command": record.command,
                        "args": record.args,
                        "url": record.url,
                        "headers": record.headers,
                        "env": record.env,
                    },
                )
            )
        return items

    @staticmethod
    def _refresh_mcp_pool_background(mcp_config: MCPConfig) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(MCPConnectionPool.refresh_in_background(mcp_config))

    async def update_mcp_server(
        self,
        server_name: str,
        mcp_config: MCPConfig,
        *,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
        is_admin: bool = False,
    ) -> MCPConfig:
        if self._mcp_server_service is None:
            raise BadRequestError("MCP 服务未启用", error_key="errors.mcpNotEnabled")
        cfg = mcp_config.mcpServers.get(server_name)
        if cfg is None:
            raise BadRequestError(f"MCP 配置中缺少服务[{server_name}]")
        if not is_admin and cfg.transport == MCPTransport.STDIO:
            raise ForbiddenError("仅管理员可配置 stdio 类型的 MCP 服务")
        records = await self._mcp_server_service.list_servers(mask=False, scope=scope)
        target = next((record for record in records if record.name == server_name), None)
        if target is None:
            raise NotFoundError(f"该MCP服务[{server_name}]不存在，请核实后重试")
        updated = MCPServerRecord(
            id=target.id,
            name=server_name,
            transport=cfg.transport,
            enabled=cfg.enabled,
            description=cfg.description,
            env=cfg.env,
            command=cfg.command,
            args=cfg.args,
            url=cfg.url,
            headers=cfg.headers,
            owner_user_id=target.owner_user_id,
            visibility=target.visibility,
        )
        await self._mcp_server_service.update_server(
            target.id,
            updated,
            scope=scope,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
        )
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()
        return await self._mcp_server_service.resolve_mcp_config(scope)

    async def update_and_create_mcp_servers(
        self,
        mcp_config: MCPConfig,
        *,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
        is_admin: bool = False,
    ) -> MCPConfig:
        if self._mcp_server_service is None:
            raise BadRequestError("MCP 服务未启用", error_key="errors.mcpNotEnabled")
        for name, cfg in mcp_config.mcpServers.items():
            if not is_admin and cfg.transport == MCPTransport.STDIO:
                raise ForbiddenError("仅管理员可配置 stdio 类型的 MCP 服务")
            record = MCPServerRecord(
                id=str(uuid.uuid4()),
                name=name,
                transport=cfg.transport,
                enabled=cfg.enabled,
                description=cfg.description,
                env=cfg.env,
                command=cfg.command,
                args=cfg.args,
                url=cfg.url,
                headers=cfg.headers,
            )
            if not is_admin:
                from app.domain.models.llm_model import ResourceVisibility
                record.visibility = ResourceVisibility.PRIVATE
            await self._mcp_server_service.create_server(
                record,
                scope=scope,
                actor_user_id=actor_user_id,
                is_admin=is_admin,
            )
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()
        return await self._mcp_server_service.resolve_mcp_config(scope)

    async def delete_mcp_server(
        self,
        server_name: str,
        *,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
    ) -> None:
        if self._mcp_server_service is None:
            raise BadRequestError("MCP 服务未启用", error_key="errors.mcpNotEnabled")
        records = await self._mcp_server_service.list_servers(mask=False, scope=scope)
        target = next((r for r in records if r.name == server_name), None)
        if target is None:
            raise NotFoundError(f"该MCP服务[{server_name}]不存在，请核实后重试")
        await self._mcp_server_service.delete_server(target.id, scope=scope, actor_user_id=actor_user_id)
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()

    async def set_mcp_server_enabled(
        self,
        server_name: str,
        enabled: bool,
        *,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
    ) -> None:
        if self._mcp_server_service is None:
            raise BadRequestError("MCP 服务未启用", error_key="errors.mcpNotEnabled")
        records = await self._mcp_server_service.list_servers(mask=False, scope=scope)
        target = next((r for r in records if r.name == server_name), None)
        if target is None:
            raise NotFoundError(f"该MCP服务[{server_name}]不存在，请核实后重试")
        await self._mcp_server_service.set_enabled(target.id, enabled, scope=scope, actor_user_id=actor_user_id)
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()

    async def create_a2a_server(
        self,
        base_url: str,
        *,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
        is_admin: bool = False,
    ) -> A2AConfig:
        if self._a2a_server_service is None:
            raise BadRequestError("A2A 服务未启用")
        from app.domain.models.llm_model import ResourceVisibility
        visibility = ResourceVisibility.GLOBAL if is_admin else ResourceVisibility.PRIVATE
        await self._a2a_server_service.create_server(
            base_url,
            scope=scope,
            actor_user_id=actor_user_id,
            visibility=visibility,
        )
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()
        return await self._a2a_server_service.resolve_a2a_config(scope)

    async def get_a2a_servers(self, scope: Optional[OwnerScope] = None) -> List[ListA2AServerItem]:
        if self._a2a_server_service is None:
            raise BadRequestError("A2A 服务未启用")
        app_config = AppConfig(a2a_config=await self._a2a_server_service.resolve_a2a_config(scope))
        a2a_client_manager = await A2AConnectionPool.acquire(app_config.a2a_config)
        agent_cards = a2a_client_manager.agent_cards
        return [
            ListA2AServerItem(
                id=id,
                name=agent_card.get("name", ""),
                description=agent_card.get("description", ""),
                input_modes=agent_card.get("defaultInputModes", []),
                output_modes=agent_card.get("defaultOutputModes", []),
                streaming=agent_card.get("capabilities", {}).get("streaming", False),
                push_notifications=agent_card.get("capabilities", {}).get("push_notifications", False),
                enabled=agent_card.get("enabled", False),
            )
            for id, agent_card in agent_cards.items()
        ]

    async def set_a2a_server_enabled(
        self,
        a2a_id: str,
        enabled: bool,
        *,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
    ) -> None:
        if self._a2a_server_service is None:
            raise BadRequestError("A2A 服务未启用")
        await self._a2a_server_service.set_enabled(a2a_id, enabled, scope=scope, actor_user_id=actor_user_id)
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()

    async def delete_a2a_server(
        self,
        a2a_id: str,
        *,
        scope: Optional[OwnerScope] = None,
        actor_user_id: Optional[str] = None,
    ) -> None:
        if self._a2a_server_service is None:
            raise BadRequestError("A2A 服务未启用")
        await self._a2a_server_service.delete_server(a2a_id, scope=scope, actor_user_id=actor_user_id)
        self._invalidate_runtime_pools()
        self._notify_config_invalidate()
