#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Memory recall scoring with time decay."""
import math
from datetime import datetime, timezone
from typing import List

from app.domain.models.memory_entry import MemoryEntry


def _entry_age_hours(entry: MemoryEntry, now: datetime) -> float:
    anchor = entry.last_used_at or entry.created_at or now
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return max(0.0, (now - anchor).total_seconds() / 3600.0)


def rank_entries_with_decay(entries: List[MemoryEntry], limit: int) -> List[MemoryEntry]:
    """Rank memories by recency decay and usage reinforcement."""
    now = datetime.now(timezone.utc)
    scored = []
    for entry in entries:
        age_hours = _entry_age_hours(entry, now)
        decay = math.exp(-age_hours / 168.0)
        usage_boost = 1.0 + math.log1p(entry.use_count or 0)
        scored.append((decay * usage_boost, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in scored[:limit]]
