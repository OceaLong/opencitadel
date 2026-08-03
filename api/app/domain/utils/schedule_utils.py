#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Schedule helpers without extra dependencies."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def compute_next_run(
    trigger_type: str,
    trigger_spec: str,
    *,
    from_time: Optional[datetime] = None,
    timezone_name: str = "UTC",
) -> Optional[datetime]:
    now = from_time or datetime.now()
    spec = (trigger_spec or "").strip()
    if trigger_type == "interval":
        try:
            seconds = int(spec)
        except ValueError:
            seconds = 3600
        return now + timedelta(seconds=max(seconds, 60))
    if trigger_type == "cron":
        return _next_cron(spec, now, timezone_name)
    if trigger_type == "webhook":
        return None
    return now + timedelta(hours=1)


def _next_cron(spec: str, now: datetime, timezone_name: str = "UTC") -> datetime:
    """Minimal daily cron: 'HH:MM' or 'minute hour * * *' five-field."""
    spec = spec.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", spec):
        hour, minute = map(int, spec.split(":"))
    else:
        parts = spec.split()
        if len(parts) != 5 or parts[2:] != ["*", "*", "*"] or not parts[0].isdigit() or not parts[1].isdigit():
            return now + timedelta(hours=1)
        minute, hour = int(parts[0]), int(parts[1])
    if not 0 <= minute <= 59 or not 0 <= hour <= 23:
        raise ValueError("cron minute/hour out of range")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid IANA timezone: {timezone_name}") from exc

    input_was_naive = now.tzinfo is None
    now_utc = now.replace(tzinfo=timezone.utc) if input_was_naive else now.astimezone(timezone.utc)
    local_now = now_utc.astimezone(zone)
    for day_offset in range(0, 370):
        local_date = (local_now + timedelta(days=day_offset)).date()
        local_candidate = datetime(local_date.year, local_date.month, local_date.day, hour, minute)
        aware = local_candidate.replace(tzinfo=zone, fold=0)
        candidate_utc = aware.astimezone(timezone.utc)
        # A DST gap round-trip changes the wall clock; skip it to the next legal daily occurrence.
        if candidate_utc.astimezone(zone).replace(tzinfo=None) != local_candidate:
            continue
        if candidate_utc > now_utc:
            return candidate_utc.replace(tzinfo=None) if input_was_naive else candidate_utc
    raise ValueError("could not compute next daily run")


def render_prompt_template(template: str, payload: dict | None = None) -> str:
    result = template or ""
    payload = payload or {}
    for key, value in payload.items():
        result = result.replace(f"{{{{payload.{key}}}}}", str(value))
    return result
