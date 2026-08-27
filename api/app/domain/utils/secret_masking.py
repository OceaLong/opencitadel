"""Pure presentation-safe masking for credentials held by domain models."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def mask_string_value(value: str) -> str:
    return mask_secret(value)


def mask_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url

    netloc = parsed.netloc
    if parsed.password is not None:
        username = parsed.username or ""
        masked_password = mask_secret(parsed.password)
        hostname = parsed.hostname or ""
        if parsed.port is not None:
            hostname = f"{hostname}:{parsed.port}"
        netloc = f"{username}:{masked_password}@{hostname}" if username else hostname

    query = parsed.query
    if query:
        query = urlencode(
            [
                (key, mask_secret(value) if value else value)
                for key, value in parse_qsl(query, keep_blank_values=True)
            ],
            safe="*",
        )

    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, parsed.fragment))


__all__ = ["mask_secret", "mask_string_value", "mask_url"]
