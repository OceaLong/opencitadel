#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Iterable, Optional

from app.domain.utils.outbound_url import Resolver, resolve_outbound_url


def validate_mcp_http_url(
    url: str,
    *,
    context: str = "MCP URL",
    resolver: Optional[Resolver] = None,
    resolve_dns: bool = True,
    allowed_ports: Optional[Iterable[int]] = None,
) -> None:
    """Validate MCP HTTP/SSE targets against the central outbound policy.

    ``allowed_ports`` defaults to None, in which case resolve_outbound_url()
    falls back to its own conservative DEFAULT_OUTBOUND_PORTS
    ({80, 443, 8080, 8443}). Callers that operate under Settings (i.e. the
    application/domain-service layers, not pure unit tests) should pass
    Settings.outbound_allowed_ports (see
    app.domain.utils.outbound_url.parse_allowed_ports) so operator-registered
    MCP servers on non-default ports -- e.g. the bundled Ops Patrol Collector
    on 8090 -- aren't unconditionally rejected regardless of configuration.
    """
    kwargs = {"resolve_dns": resolve_dns}
    if resolver is not None:
        kwargs["resolver"] = resolver
    if allowed_ports is not None:
        kwargs["allowed_ports"] = allowed_ports
    try:
        resolve_outbound_url(url, **kwargs)
    except ValueError as exc:
        raise ValueError(f"{context}: {exc}") from exc
