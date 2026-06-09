#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.utils.app_config_filter import (
    filter_a2a_config_by_refs,
    filter_enabled_a2a_config,
    filter_enabled_mcp_config,
    filter_mcp_config_by_refs,
)
from app.domain.models.app_config import A2AConfig, A2AServerConfig, MCPConfig, MCPServerConfig, MCPTransport


def test_filter_enabled_mcp_config():
    cfg = MCPConfig(mcpServers={
        "a": MCPServerConfig(transport=MCPTransport.STREAMABLE_HTTP, url="http://a", enabled=True),
        "b": MCPServerConfig(transport=MCPTransport.STREAMABLE_HTTP, url="http://b", enabled=False),
    })
    filtered = filter_enabled_mcp_config(cfg)
    assert list(filtered.mcpServers.keys()) == ["a"]


def test_filter_enabled_a2a_config():
    cfg = A2AConfig(a2a_servers=[
        A2AServerConfig(id="1", base_url="http://a", enabled=True),
        A2AServerConfig(id="2", base_url="http://b", enabled=False),
    ])
    filtered = filter_enabled_a2a_config(cfg)
    assert len(filtered.a2a_servers) == 1
    assert filtered.a2a_servers[0].id == "1"


def test_filter_mcp_config_by_refs():
    cfg = MCPConfig(mcpServers={
        "a": MCPServerConfig(transport=MCPTransport.STREAMABLE_HTTP, url="http://a", enabled=True),
        "b": MCPServerConfig(transport=MCPTransport.STREAMABLE_HTTP, url="http://b", enabled=True),
    })
    filtered = filter_mcp_config_by_refs(cfg, ["a"])
    assert list(filtered.mcpServers.keys()) == ["a"]


def test_filter_a2a_config_by_refs():
    cfg = A2AConfig(a2a_servers=[
        A2AServerConfig(id="1", base_url="http://a", enabled=True),
        A2AServerConfig(id="2", base_url="http://b", enabled=True),
    ])
    filtered = filter_a2a_config_by_refs(cfg, ["2"])
    assert len(filtered.a2a_servers) == 1
    assert filtered.a2a_servers[0].id == "2"
