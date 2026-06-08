#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Runtime filters for MCP/A2A configuration."""
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
