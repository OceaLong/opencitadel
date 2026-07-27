#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional

from app.domain.utils.outbound_url import Resolver, resolve_outbound_url


def validate_mcp_http_url(
    url: str,
    *,
    context: str = "MCP URL",
    resolver: Optional[Resolver] = None,
    resolve_dns: bool = True,
) -> None:
    """Validate MCP HTTP/SSE targets against the central outbound policy."""
    kwargs = {"resolve_dns": resolve_dns}
    if resolver is not None:
        kwargs["resolver"] = resolver
    try:
        resolve_outbound_url(url, **kwargs)
    except ValueError as exc:
        raise ValueError(f"{context}: {exc}") from exc
