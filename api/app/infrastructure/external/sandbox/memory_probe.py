#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Host-level memory probe for sandbox admission (docker driver)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_host_available_mb() -> int | None:
    """Return available memory in MB from host /proc view, or None on failure."""
    try:
        import psutil

        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception as exc:
        logger.warning("Memory probe failed: %s", exc)
        return None


def memory_meets_threshold(min_available_mb: int) -> bool:
    """True if host available memory is at or above threshold."""
    available = get_host_available_mb()
    if available is None:
        return False
    return available >= min_available_mb
