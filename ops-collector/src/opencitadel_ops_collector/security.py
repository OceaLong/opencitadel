"""Allowlist enforcement shared by every probe."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from .config import CollectorSettings


class ProbeDenied(ValueError):
    pass


def require_namespace(settings: CollectorSettings, namespace: str) -> None:
    if namespace not in settings.allowed_namespaces:
        raise ProbeDenied("NAMESPACE_DENIED")


def require_workloads(settings: CollectorSettings, namespace: str, workload_ids: list[str]) -> None:
    require_namespace(settings, namespace)
    configured = settings.allowed_workloads.get(namespace, [])
    if configured and any(item not in configured for item in workload_ids):
        raise ProbeDenied("PROBE_DENIED")


def require_registered(registry: dict, identifier: str) -> None:
    if identifier not in registry:
        raise ProbeDenied("PROBE_DENIED")


def validate_redirect(original_url: str, redirected_url: str) -> None:
    original = urlparse(original_url)
    redirected = urlparse(redirected_url)
    if redirected.scheme not in {"http", "https"} or redirected.hostname != original.hostname:
        raise ProbeDenied("PROBE_DENIED")
    try:
        address = ipaddress.ip_address(redirected.hostname or "")
    except ValueError:
        return
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise ProbeDenied("PROBE_DENIED")
