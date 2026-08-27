"""Pure slug normalization shared by domain-facing services."""

from __future__ import annotations

import re


def slugify(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^\w\s-]", "", value.lower())
    return re.sub(r"[-\s]+", "-", normalized).strip("-") or fallback


__all__ = ["slugify"]
