#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Runtime filters for MCP/A2A configuration."""
from typing import List, Optional

from app.domain.models.app_config import A2AConfig, MCPConfig


def filter_enabled_mcp_config(mcp_config: MCPConfig) -> MCPConfig:
    """Return MCP config containing only enabled servers for agent runtime."""
    enabled = {
        name: cfg
        for name, cfg in mcp_config.mcpServers.items()
        if cfg.enabled
    }
    return MCPConfig(mcpServers=enabled)


def filter_enabled_a2a_config(a2a_config: A2AConfig) -> A2AConfig:
    """Return A2A config containing only enabled servers for agent runtime."""
    enabled = [server for server in a2a_config.a2a_servers if server.enabled]
    return A2AConfig(a2a_servers=enabled)


def filter_mcp_config_by_refs(
        mcp_config: MCPConfig,
        server_refs: Optional[List[str]] = None,
) -> MCPConfig:
    """按 Skill 引用的 server 名过滤 MCP 配置；无引用时返回全部已启用服务。"""
    enabled = filter_enabled_mcp_config(mcp_config)
    if not server_refs:
        return enabled
    refs = set(server_refs)
    filtered = {
        name: cfg
        for name, cfg in enabled.mcpServers.items()
        if name in refs
    }
    return MCPConfig(mcpServers=filtered)


def filter_a2a_config_by_refs(
        a2a_config: A2AConfig,
        server_refs: Optional[List[str]] = None,
) -> A2AConfig:
    """按 Skill 引用的 server id 过滤 A2A 配置；无引用时返回全部已启用服务。"""
    enabled = filter_enabled_a2a_config(a2a_config)
    if not server_refs:
        return enabled
    refs = set(server_refs)
    filtered = [server for server in enabled.a2a_servers if server.id in refs]
    return A2AConfig(a2a_servers=filtered)
