#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Derive client IP only across explicitly trusted reverse-proxy hops."""
from __future__ import annotations

import ipaddress
from typing import Iterable

from starlette.requests import Request

from core.config import get_settings

_MAX_FORWARDED_HEADER_BYTES = 1024
_MAX_PROXY_HOPS = 16


def _networks(values: Iterable[str]) -> tuple[ipaddress._BaseNetwork, ...]:
    return tuple(
        ipaddress.ip_network(value.strip(), strict=False)
        for value in values
        if value.strip()
    )


def configured_trusted_proxy_cidrs() -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in get_settings().trusted_proxy_cidrs.split(",")
        if value.strip()
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
        else configured_trusted_proxy_cidrs()
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

    for hop in reversed(hops):
        if any(hop in network for network in networks):
            continue
        return str(hop)
    return str(hops[0])
