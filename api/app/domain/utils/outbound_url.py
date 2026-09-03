"""Fail-closed validation and DNS resolution for server-side outbound URLs."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

Resolver = Callable[..., Sequence[tuple]]

DEFAULT_OUTBOUND_PORTS = frozenset({80, 443, 8080, 8443})


def parse_allowed_ports(value: str) -> frozenset[int]:
    """Parse DeploymentSettings.outbound_allowed_ports into a port set.

    Shared by every caller that uses the configured allowlist rather than the
    conservative module default.
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
        # RFC 2544 benchmarking space: newer Docker Desktop allocates container
        # networks from it, so an explicitly allowlisted internal hostname may
        # resolve here. It carries no metadata/loopback/link-local semantics
        # and is not routable on the public internet, so it sits at the same
        # trust level as the RFC1918 entries — reachable only via the explicit
        # private-host allowlist, never by default.
        "198.18.0.0/15",
        "fc00::/7",
    )
)
# Address-translation / relay ranges that Python's ``is_global`` reports as
# public but which route onto private, link-local or metadata space and must
# always be rejected. NAT64 lets ``[64:ff9b::a9fe:a9fe]`` resolve to
# 169.254.169.254, and 6to4 relay anycast bridges to reserved space. IPv4
# addresses embedded in IPv6 (NAT64 / IPv4-mapped) are unwrapped separately in
# ``_effective_ip`` so the existing private-range rules apply to them too.
_TRANSLATION_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "64:ff9b::/96",  # NAT64 well-known prefix (RFC 6052)
        "192.88.99.0/24",  # 6to4 relay anycast (RFC 7526)
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
        if pattern and (hostname == pattern or hostname.endswith(f".{pattern}")):
            return True
    return False


def _private_host_explicitly_allowed(
    hostname: str,
    allow_private_hosts: Iterable[str],
) -> bool:
    return hostname in {_normalise_hostname(item) for item in allow_private_hosts if item.strip()}


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


def _effective_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap an IPv4-mapped IPv6 address (``::ffff:0:0/96``) to its IPv4 form.

    Python treats IPv4-mapped addresses like ``::ffff:169.254.169.254`` as
    IPv6, so the embedded IPv4 must be recovered before the private-range rules
    below can recognise it.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


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
        effective_ip = _effective_ip(ip)
        for candidate in {ip, effective_ip}:
            if any(
                candidate.version == network.version and candidate in network
                for network in _TRANSLATION_BLOCKED_NETWORKS
            ):
                raise OutboundURLRejected(
                    f"不允许访问 NAT64/6to4 等地址转换或中继范围: {hostname} ({ip})"
                )
        if effective_ip.is_global:
            continue
        if not private_allowed:
            raise OutboundURLRejected(f"不允许访问内网、本地、保留或元数据地址: {hostname} ({ip})")
        if not any(
            effective_ip.version == network.version and effective_ip in network
            for network in _OPERATOR_ALLOWABLE_PRIVATE_NETWORKS
        ):
            raise OutboundURLRejected(
                f"私网白名单不能覆盖元数据、环回、链路本地或保留地址: {hostname} ({ip})"
            )


def resolve_outbound_url(
    url: str,
    *,
    allowed_ports: Iterable[int] = DEFAULT_OUTBOUND_PORTS,
    allowlist: Iterable[str] = (),
    denylist: Iterable[str] = (),
    allow_private_hosts: Iterable[str] = (),
    resolver: Resolver | None = None,
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
        hostname == "localhost" or hostname.endswith(_BLOCKED_HOST_SUFFIXES)
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
