#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.services.tools.tool_names import is_tool_allowed


def test_is_tool_allowed_none_means_all():
    assert is_tool_allowed("read_file", None) is True
    assert is_tool_allowed("mcp_jina_read_url", None) is True


def test_is_tool_allowed_exact_match():
    allowed = ["read_file", "write_file"]
    assert is_tool_allowed("read_file", allowed) is True
    assert is_tool_allowed("search_web", allowed) is False


def test_is_tool_allowed_mcp_wildcard():
    allowed = ["mcp_*"]
    assert is_tool_allowed("mcp_jina_read_url", allowed) is True
    assert is_tool_allowed("mcp_amap_maps_search", allowed) is True
    assert is_tool_allowed("read_file", allowed) is False


def test_is_tool_allowed_mcp_server_prefix():
    allowed = ["mcp_jina_*"]
    assert is_tool_allowed("mcp_jina_read_url", allowed) is True
    assert is_tool_allowed("mcp_amap_search", allowed) is False


def test_is_tool_allowed_a2a_group():
    allowed = ["a2a"]
    assert is_tool_allowed("get_remote_agent_cards", allowed) is True
    assert is_tool_allowed("call_remote_agent", allowed) is True
    assert is_tool_allowed("read_file", allowed) is False


def test_is_tool_allowed_legacy_alias():
    allowed = ["file_read"]
    assert is_tool_allowed("read_file", allowed) is True
