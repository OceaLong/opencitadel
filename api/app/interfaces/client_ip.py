"""Derive client IP only across explicitly trusted reverse-proxy hops."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from starlette.requests import Request

from app.composition.types import ApiRuntime

_MAX_FORWARDED_HEADER_BYTES = 1024
_MAX_PROXY_HOPS = 16


def _networks(values: Iterable[str]) -> tuple[ipaddress._BaseNetwork, ...]:
    return tuple(
        ipaddress.ip_network(value.strip(), strict=False) for value in values if value.strip()
    )


def configured_trusted_proxy_cidrs(request: Request) -> tuple[str, ...]:
    application = request.scope.get("app")
    runtime = getattr(getattr(application, "state", None), "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        return ()
    return tuple(
        value.strip() for value in runtime.settings.trusted_proxy_cidrs.split(",") if value.strip()
    )


def get_client_ip(
    request: Request,
    *,
    trusted_proxy_cidrs: Iterable[str] | None = None,
) -> str:
    """Return the first untrusted hop, walking XFF from the socket peer."""
    peer_text = request.client.host if request.client else ""
    try:
        peer = ipaddress.ip_address(peer_text)
    except ValueError:
        return peer_text

    networks = _networks(
        trusted_proxy_cidrs
        if trusted_proxy_cidrs is not None
        else configured_trusted_proxy_cidrs(request)
    )
    if not any(peer in network for network in networks):
        return str(peer)

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return str(peer)
    if len(forwarded.encode("utf-8")) > _MAX_FORWARDED_HEADER_BYTES:
        return str(peer)
    raw_hops = [value.strip() for value in forwarded.split(",")]
    if not raw_hops or len(raw_hops) > _MAX_PROXY_HOPS:
        return str(peer)
    try:
        hops = [ipaddress.ip_address(value) for value in raw_hops]
    except ValueError:
        return str(peer)

    # Trust-chain-depth walk: starting from the nearest hop (the peer wrote the
    # rightmost entry), strip only addresses that belong to a trusted proxy CIDR
    # and stop at the first untrusted address — that is the real client. We never
    # blindly take the left-most (attacker-controlled) entry. The depth of trust
    # is therefore bounded by the trusted_proxy_cidrs configuration: with a single
    # narrow ingress CIDR only that one appended hop is peeled off, so an attacker
    # cannot impersonate an arbitrary client by prepending extra XFF entries.
    # (config.py forbids broad RFC1918 CIDRs in production so the sandbox network,
    # which shares those ranges, is never treated as a trusted proxy layer.)
    for hop in reversed(hops):
        if any(hop in network for network in networks):
            continue
        return str(hop)
    # Every hop was a trusted proxy: fall back to the left-most claimed origin.
    return str(hops[0])
