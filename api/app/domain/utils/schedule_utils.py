"""Schedule helpers without extra dependencies."""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def compute_next_run(
    trigger_type: str,
    trigger_spec: str,
    *,
    from_time: datetime | None = None,
    timezone_name: str = "UTC",
) -> datetime | None:
    now = from_time or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("from_time must be timezone-aware")
    now = now.astimezone(UTC)
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
        if (
            len(parts) != 5
            or parts[2:] != ["*", "*", "*"]
            or not parts[0].isdigit()
            or not parts[1].isdigit()
        ):
            return now + timedelta(hours=1)
        minute, hour = int(parts[0]), int(parts[1])
    if not 0 <= minute <= 59 or not 0 <= hour <= 23:
        raise ValueError("cron minute/hour out of range")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid IANA timezone: {timezone_name}") from exc

    now_utc = now.astimezone(UTC)
    local_now = now_utc.astimezone(zone)
    for day_offset in range(370):
        local_date = (local_now + timedelta(days=day_offset)).date()
        # Cron fields describe a wall-clock value; attach the IANA zone only
        # after constructing that deliberately zone-less clock reading.
        local_candidate = datetime.combine(local_date, time(hour, minute))
        aware = local_candidate.replace(tzinfo=zone, fold=0)
        candidate_utc = aware.astimezone(UTC)
        # A DST gap round-trip changes the wall clock; skip it to the next legal daily occurrence.
        if candidate_utc.astimezone(zone).replace(tzinfo=None) != local_candidate:
            continue
        if candidate_utc > now_utc:
            return candidate_utc
    raise ValueError("could not compute next daily run")


def validate_trigger_spec(trigger_type: str, trigger_spec: str) -> None:
    """Reject schedules that compute_next_run would silently degrade to hourly.

    The scheduler intentionally has no cron dependency, so only 'HH:MM' and
    'minute hour * * *' daily cron are supported. Without this gate a spec like
    '*/15 * * * *' was accepted and silently run once an hour instead.
    """
    spec = (trigger_spec or "").strip()
    if trigger_type == "interval":
        if not spec.isdigit():
            raise ValueError("interval 触发的 trigger_spec 必须是表示秒数的整数")
        return
    if trigger_type == "cron":
        if re.fullmatch(r"\d{1,2}:\d{2}", spec):
            hour, minute = map(int, spec.split(":"))
        else:
            parts = spec.split()
            if (
                len(parts) != 5
                or parts[2:] != ["*", "*", "*"]
                or not parts[0].isdigit()
                or not parts[1].isdigit()
            ):
                raise ValueError(
                    "不支持的 cron 表达式：仅支持 'HH:MM' 或 'minute hour * * *' 每日计划"
                )
            minute, hour = int(parts[0]), int(parts[1])
        if not 0 <= minute <= 59 or not 0 <= hour <= 23:
            raise ValueError("cron 的分钟/小时超出范围")
        return


def render_prompt_template(template: str, payload: dict | None = None) -> str:
    result = template or ""
    payload = payload or {}
    for key, value in payload.items():
        result = result.replace(f"{{{{payload.{key}}}}}", str(value))
    return result
