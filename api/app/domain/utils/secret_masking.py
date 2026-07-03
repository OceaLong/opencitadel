#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Mask sensitive values for display/API responses."""
from __future__ import annotations

from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def mask_string_value(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def mask_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    netloc = parsed.netloc
    if parsed.password is not None:
        username = parsed.username or ""
        masked_password = mask_string_value(parsed.password) if parsed.password else ""
        hostname = parsed.hostname or ""
        if parsed.port is not None:
            hostname = f"{hostname}:{parsed.port}"
        if username:
            netloc = f"{username}:{masked_password}@{hostname}"
        else:
            netloc = hostname

    query = parsed.query
    if query:
        masked_pairs = [
            (key, mask_string_value(value) if value else value)
            for key, value in parse_qsl(query, keep_blank_values=True)
        ]
        query = urlencode(masked_pairs, safe="*")

    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, parsed.fragment))
