#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Schedule helpers without extra dependencies."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional


def compute_next_run(trigger_type: str, trigger_spec: str, *, from_time: Optional[datetime] = None) -> Optional[datetime]:
    now = from_time or datetime.now()
    spec = (trigger_spec or "").strip()
    if trigger_type == "interval":
        try:
            seconds = int(spec)
        except ValueError:
            seconds = 3600
        return now + timedelta(seconds=max(seconds, 60))
    if trigger_type == "cron":
        return _next_cron(spec, now)
    if trigger_type == "webhook":
        return None
    return now + timedelta(hours=1)


def _next_cron(spec: str, now: datetime) -> datetime:
    """Minimal daily cron: 'HH:MM' or 'minute hour * * *' five-field."""
    spec = spec.strip()
    if re.match(r"^\d{1,2}:\d{2}$", spec):
        hour, minute = map(int, spec.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    parts = spec.split()
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        minute, hour = int(parts[0]), int(parts[1])
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    return now + timedelta(hours=1)


def render_prompt_template(template: str, payload: dict | None = None) -> str:
    result = template or ""
    payload = payload or {}
    for key, value in payload.items():
        result = result.replace(f"{{{{payload.{key}}}}}", str(value))
    return result
