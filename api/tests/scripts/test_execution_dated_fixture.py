"""Pure clock-plan checks; these do not claim persisted analysis evidence."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from scripts.acceptance.strict_driver.dated_plan import dated_plan


def test_seven_completed_days_are_explicit_utc_and_exclude_today():
    now = datetime(2026, 9, 17, 13, 44, tzinfo=UTC)
    start, end, instants = dated_plan(now)
    assert start == datetime(2026, 9, 10, tzinfo=UTC)
    assert end == datetime(2026, 9, 17, tzinfo=UTC)
    assert len(set(instants)) == 7
    assert all(start <= value < end for value in instants)
    assert all(value.hour == 12 for value in instants)
    assert all(b - a == timedelta(days=1) for a, b in pairwise(instants))


def test_naive_time_is_rejected_instead_of_using_local_timezone():
    with pytest.raises(ValueError, match="aware"):
        dated_plan(datetime(2026, 9, 17))  # noqa: DTZ001 - rejection test
