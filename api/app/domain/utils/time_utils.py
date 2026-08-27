"""Timezone-aware UTC datetime helpers."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current instant as an aware UTC datetime."""
    return datetime.now(UTC)


def to_utc(dt: datetime | None) -> datetime | None:
    """Normalize an aware datetime to UTC and reject ambiguous naive values."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(UTC)
