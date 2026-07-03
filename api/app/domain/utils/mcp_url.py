#!/usr/bin/env python
# -*- coding: utf-8 -*-
from urllib.parse import urlparse


def validate_mcp_http_url(url: str, *, context: str = "MCP URL") -> None:
    """Ensure MCP HTTP/SSE URLs include an explicit http(s) scheme."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{context} 必须以 http:// 或 https:// 开头")
