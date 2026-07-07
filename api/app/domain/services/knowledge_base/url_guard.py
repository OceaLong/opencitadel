#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SSRF-safe URL validation for knowledge-base web ingestion."""
import ipaddress
import socket
from typing import Iterable, Optional
from urllib.parse import urlparse

from app.application.errors.exceptions import BadRequestError
from app.application.services.config_provider import get_runtime_config

_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved or ip.is_multicast:
        return True
    for network in _BLOCKED_NETWORKS:
        if ip in network:
            return True
    return False


def _host_allowed(hostname: str, allowlist: Iterable[str]) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    for entry in allowlist:
        pattern = entry.strip().lower()
        if not pattern:
            continue
        if host == pattern or host.endswith(f".{pattern}"):
            return True
    return False


def _resolve_and_check(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BadRequestError(f"无法解析 URL 主机: {hostname}") from exc
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip):
            raise BadRequestError(f"不允许访问内网或本地地址: {hostname} ({ip})", error_key="errors.urlNotAllowed")


def validate_public_url(url: str, *, allowlist: Optional[list[str]] = None) -> str:
    """Validate URL scheme/host and block private/metadata targets."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise BadRequestError("仅支持 http/https 链接")
    hostname = parsed.hostname
    if not hostname:
        raise BadRequestError("URL 缺少有效主机名")

    cfg = get_runtime_config().knowledge_base.connectors
    effective_allowlist = allowlist if allowlist is not None else (cfg.url_allowlist or [])
    denylist = cfg.url_denylist or []

    host_lower = hostname.lower()
    if any(host_lower == item.lower() or host_lower.endswith(f".{item.lower()}") for item in denylist if item):
        raise BadRequestError(f"URL 主机在禁止列表中: {hostname}")

    if effective_allowlist and not _host_allowed(hostname, effective_allowlist):
        raise BadRequestError(f"URL 主机不在允许列表中: {hostname}", error_key="errors.urlHostNotAllowed")

    _resolve_and_check(hostname)
    return url.strip()
