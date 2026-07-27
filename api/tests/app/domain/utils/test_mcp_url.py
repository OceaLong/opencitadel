#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.models.app_config import MCPServerConfig, MCPTransport
from app.domain.models.integration_server import MCPServerRecord
from app.domain.utils.mcp_url import validate_mcp_http_url


def test_validate_mcp_http_url_accepts_https():
    validate_mcp_http_url(
        "https://mcp.example.com/mcp",
        resolver=lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )


def test_validate_mcp_http_url_rejects_missing_scheme():
    with pytest.raises(ValueError, match="http://"):
        validate_mcp_http_url("mcp.example.com/mcp")


def test_mcp_server_config_rejects_missing_scheme():
    with pytest.raises(ValueError, match="http://"):
        MCPServerConfig(
            transport=MCPTransport.STREAMABLE_HTTP,
            url="mcp.example.com/mcp",
            enabled=True,
        )


def test_mcp_server_record_rejects_missing_scheme():
    with pytest.raises(ValueError, match="http://"):
        MCPServerRecord(
            id="srv-1",
            name="bad",
            transport=MCPTransport.SSE,
            url="example.com/sse",
            enabled=True,
        )


def test_validate_mcp_http_url_rejects_private_dns():
    with pytest.raises(ValueError, match="内网"):
        validate_mcp_http_url(
            "https://mcp.example.com/mcp",
            resolver=lambda *args, **kwargs: [
                (2, 1, 6, "", ("10.0.0.8", 443)),
            ],
        )
