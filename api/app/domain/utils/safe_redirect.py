#!/usr/bin/env python
# -*- coding: utf-8 -*-
from urllib.parse import urlparse


def resolve_safe_redirect_path(redirect: str | None, *, default: str = "/") -> str:
    """Return a same-origin relative path for post-login redirects."""
    candidate = (redirect or "").strip()
    if not candidate:
        return default
    if not candidate.startswith("/"):
        return default
    if candidate.startswith("//"):
        return default
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return default
    return candidate
