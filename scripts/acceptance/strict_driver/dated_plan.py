"""Controlled historical event-clock plan; current policy is resolved separately."""

from datetime import UTC, timedelta


def dated_plan(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("dated fixture requires aware time")
    end = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    return start, end, tuple(start + timedelta(days=index, hours=12) for index in range(7))
