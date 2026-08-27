from collections.abc import Iterable

from app.domain.utils.outbound_url import Resolver, resolve_outbound_url


def validate_mcp_http_url(
    url: str,
    *,
    context: str = "MCP URL",
    resolver: Resolver | None = None,
    resolve_dns: bool = True,
    allowed_ports: Iterable[int] | None = None,
    allow_private_hosts: Iterable[str] | None = None,
) -> None:
    """Validate MCP HTTP/SSE targets against the central outbound policy.

    Application callers pass the configured outbound port set. Standalone
    validation uses the conservative central default.
    """
    kwargs = {"resolve_dns": resolve_dns}
    if resolver is not None:
        kwargs["resolver"] = resolver
    if allowed_ports is not None:
        kwargs["allowed_ports"] = allowed_ports
    if allow_private_hosts is not None:
        kwargs["allow_private_hosts"] = allow_private_hosts
    try:
        resolve_outbound_url(url, **kwargs)
    except ValueError as exc:
        raise ValueError(f"{context}: {exc}") from exc
