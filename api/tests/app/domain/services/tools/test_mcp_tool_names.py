#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.services.tools.mcp import build_mcp_tool_name


def test_build_mcp_tool_name_simple():
    name = build_mcp_tool_name("jina", "read_url")
    assert name == "mcp_jina_read_url"
    assert len(name) <= 64


def test_build_mcp_tool_name_sanitizes_invalid_chars():
    name = build_mcp_tool_name("amap.maps", "read-url")
    assert "." not in name
    assert name.startswith("mcp_")


def test_build_mcp_tool_name_truncates_long_names():
    long_server = "a" * 40
    long_tool = "b" * 40
    name = build_mcp_tool_name(long_server, long_tool)
    assert len(name) <= 64
    assert name.startswith("mcp_")


def test_build_mcp_tool_name_unicode_server():
    name = build_mcp_tool_name("地图服务", "查询")
    assert len(name) <= 64
    assert all(ch.isascii() for ch in name)
