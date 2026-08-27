from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.utils.time_utils import to_utc


def test_aware_datetime_converted_to_utc():
    aware = datetime(2026, 7, 19, 18, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    result = to_utc(aware)
    assert result == datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_utc_aware_is_preserved_without_shift():
    aware = datetime(2026, 7, 19, 10, 3, 7, tzinfo=UTC)
    result = to_utc(aware)
    assert result == datetime(2026, 7, 19, 10, 3, 7, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_naive_datetime_is_rejected():
    naive = datetime(2026, 7, 19, 10, 3, 7, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        to_utc(naive)


def test_none_passthrough():
    assert to_utc(None) is None
