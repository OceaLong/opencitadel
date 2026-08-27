"""Immutable Web Operator network boundary helpers."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit


def normalize_operator_domains(values: Iterable[str]) -> list[str]:
    """Return canonical exact host names, rejecting URL-like configuration noise."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        parsed = urlsplit(value if "://" in value else f"//{value}")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("operator domains must contain host names only")
        host = parsed.hostname
        if not host or "*" in host:
            raise ValueError("operator domains must be exact host names")
        canonical = host.rstrip(".").encode("idna").decode("ascii").lower()
        if canonical not in seen:
            normalized.append(canonical)
            seen.add(canonical)
    return normalized


def assert_operator_url_allowed(url: str, allowed_domains: Iterable[str]) -> None:
    """Reject every HTTP target outside the immutable exact-host allowlist."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("operator navigation requires an absolute HTTP(S) URL")
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if host not in frozenset(allowed_domains):
        raise PermissionError(f"operator domain is not allowed: {host}")


__all__ = ["assert_operator_url_allowed", "normalize_operator_domains"]
