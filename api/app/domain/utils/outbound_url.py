#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fail-closed validation and DNS resolution for server-side outbound URLs."""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence
from urllib.parse import urlparse

Resolver = Callable[..., Sequence[tuple]]

DEFAULT_OUTBOUND_PORTS = frozenset({80, 443, 8080, 8443})


def parse_allowed_ports(value: str) -> frozenset[int]:
    """Parse Settings.outbound_allowed_ports ("80,443,...") into a port set.

    Shared by every caller that wants the *configured* allowlist instead of
    this module's own conservative DEFAULT_OUTBOUND_PORTS fallback (mirrors
    the inline set-comprehension previously duplicated in
    llm_endpoint_service.py's _validate_endpoint()).
    """
    return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
_ALWAYS_BLOCKED_HOSTS = frozenset(
    {
        "instance-data",
        "metadata",
        "metadata.google.internal",
    }
)
_BLOCKED_HOST_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
)
_OPERATOR_ALLOWABLE_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
    )
)


class OutboundURLRejected(ValueError):
    """Raised when an outbound target does not satisfy the SSRF policy."""


@dataclass(frozen=True)
class ResolvedOutboundURL:
    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def _normalise_hostname(hostname: str) -> str:
    host = hostname.strip().lower().rstrip(".")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OutboundURLRejected("URL 主机名无效") from exc


def _host_matches(hostname: str, patterns: Iterable[str]) -> bool:
    for value in patterns:
        pattern = _normalise_hostname(value)
        if pattern and (
            hostname == pattern
            or hostname.endswith(f".{pattern}")
        ):
            return True
    return False


def _private_host_explicitly_allowed(
    hostname: str,
    allow_private_hosts: Iterable[str],
) -> bool:
    return hostname in {
        _normalise_hostname(item)
        for item in allow_private_hosts
        if item.strip()
    }


def _resolve_addresses(
    hostname: str,
    port: int,
    resolver: Resolver,
) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return (str(literal),)

    try:
        answers = resolver(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except (socket.gaierror, OSError) as exc:
        raise OutboundURLRejected(f"无法解析 URL 主机: {hostname}") from exc

    addresses: list[str] = []
    for answer in answers:
        try:
            address = str(ipaddress.ip_address(answer[4][0]))
        except (IndexError, TypeError, ValueError) as exc:
            raise OutboundURLRejected(f"URL 主机解析结果无效: {hostname}") from exc
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise OutboundURLRejected(f"URL 主机没有可用地址: {hostname}")
    return tuple(addresses)


def _ensure_safe_addresses(
    hostname: str,
    addresses: Iterable[str],
    *,
    allow_private_hosts: Iterable[str],
) -> None:
    private_allowed = _private_host_explicitly_allowed(
        hostname,
        allow_private_hosts,
    )
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_global:
            continue
        if not private_allowed:
            raise OutboundURLRejected(
                f"不允许访问内网、本地、保留或元数据地址: {hostname} ({ip})"
            )
        if not any(
            ip.version == network.version and ip in network
            for network in _OPERATOR_ALLOWABLE_PRIVATE_NETWORKS
        ):
            raise OutboundURLRejected(
                f"私网白名单不能覆盖元数据、环回、链路本地或保留地址: "
                f"{hostname} ({ip})"
            )


def resolve_outbound_url(
    url: str,
    *,
    allowed_ports: Iterable[int] = DEFAULT_OUTBOUND_PORTS,
    allowlist: Iterable[str] = (),
    denylist: Iterable[str] = (),
    allow_private_hosts: Iterable[str] = (),
    resolver: Optional[Resolver] = None,
    resolve_dns: bool = True,
) -> ResolvedOutboundURL:
    """Validate a URL and return every DNS address approved for connection."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise OutboundURLRejected("URL 必须以 http:// 或 https:// 开头")
    if parsed.username is not None or parsed.password is not None:
        raise OutboundURLRejected("URL 不允许内嵌用户凭据")
    if not parsed.hostname:
        raise OutboundURLRejected("URL 缺少有效主机名")
    hostname = _normalise_hostname(parsed.hostname)
    private_host_values = tuple(allow_private_hosts)
    private_host_allowed = _private_host_explicitly_allowed(
        hostname,
        private_host_values,
    )
    if hostname in _ALWAYS_BLOCKED_HOSTS:
        raise OutboundURLRejected(f"不允许访问本地或元数据主机: {hostname}")
    if (
        hostname == "localhost"
        or hostname.endswith(_BLOCKED_HOST_SUFFIXES)
    ) and not private_host_allowed:
        raise OutboundURLRejected(f"不允许访问本地或元数据主机: {hostname}")
    if _host_matches(hostname, denylist):
        raise OutboundURLRejected(f"URL 主机在禁止列表中: {hostname}")
    allowlist_values = tuple(allowlist)
    if allowlist_values and not _host_matches(hostname, allowlist_values):
        raise OutboundURLRejected(f"URL 主机不在允许列表中: {hostname}")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise OutboundURLRejected("URL 端口无效") from exc
    approved_ports = {int(value) for value in allowed_ports}
    if port not in approved_ports:
        raise OutboundURLRejected(f"URL 端口未获批准: {port}")

    addresses: tuple[str, ...] = ()
    if resolve_dns:
        addresses = _resolve_addresses(
            hostname,
            port,
            resolver or socket.getaddrinfo,
        )
        _ensure_safe_addresses(
            hostname,
            addresses,
            allow_private_hosts=private_host_values,
        )
    else:
        try:
            literal_address = str(ipaddress.ip_address(hostname))
        except ValueError:
            literal_address = ""
        if literal_address:
            addresses = (literal_address,)
            _ensure_safe_addresses(
                hostname,
                addresses,
                allow_private_hosts=private_host_values,
            )
    return ResolvedOutboundURL(
        url=raw,
        scheme=scheme,
        hostname=hostname,
        port=port,
        addresses=addresses,
    )
