#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone

from app.domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource
from app.domain.utils.memory_recall import rank_entries_with_decay


def test_rank_entries_with_decay_prefers_recent_and_used():
    now = datetime.now(timezone.utc)
    old = MemoryEntry(
        title="old",
        content="old",
        scope=MemoryScope.GLOBAL,
        source=MemorySource.TOOL_SAVE,
        created_at=now - timedelta(days=30),
        last_used_at=now - timedelta(days=30),
        use_count=1,
    )
    recent = MemoryEntry(
        title="recent",
        content="recent",
        scope=MemoryScope.GLOBAL,
        source=MemorySource.TOOL_SAVE,
        created_at=now - timedelta(hours=1),
        last_used_at=now - timedelta(hours=1),
        use_count=5,
    )
    ranked = rank_entries_with_decay([old, recent], limit=1)
    assert ranked[0].title == "recent"
