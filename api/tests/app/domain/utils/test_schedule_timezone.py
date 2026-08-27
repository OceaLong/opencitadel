from datetime import UTC, datetime

import pytest

from app.domain.models.scheduled_job import ScheduledJob
from app.domain.utils.schedule_utils import compute_next_run


def test_existing_job_defaults_to_utc():
    assert ScheduledJob(name="x", owner_user_id="u").timezone == "UTC"


def test_daily_cron_converts_asia_shanghai_to_utc():
    result = compute_next_run(
        "cron",
        "0 9 * * *",
        from_time=datetime(2026, 8, 3, 0, 0, tzinfo=UTC),
        timezone_name="Asia/Shanghai",
    )
    assert result == datetime(2026, 8, 3, 1, 0, tzinfo=UTC)


def test_invalid_timezone_is_rejected():
    with pytest.raises(ValueError, match="IANA"):
        ScheduledJob(name="x", owner_user_id="u", timezone="Moon/Base")


def test_interval_ignores_timezone():
    now = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
    assert compute_next_run("interval", "60", from_time=now, timezone_name="Moon/Base") == datetime(
        2026, 8, 3, 0, 1, tzinfo=UTC
    )


def test_naive_from_time_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_next_run(
            "interval",
            "60",
            from_time=datetime(2026, 8, 3, 0, 0, tzinfo=UTC).replace(tzinfo=None),
        )


def test_nonexistent_dst_time_moves_to_next_valid_occurrence():
    result = compute_next_run(
        "cron",
        "30 2 * * *",
        from_time=datetime(2026, 3, 8, 6, 0, tzinfo=UTC),
        timezone_name="America/New_York",
    )
    assert result == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


def test_repeated_dst_time_triggers_once():
    # 01:30 occurs twice; fold=0 fires once. After the first occurrence the next
    # run is the following day, not fold=1 on the same wall-clock date.
    result = compute_next_run(
        "cron",
        "30 1 * * *",
        from_time=datetime(2026, 11, 1, 5, 31, tzinfo=UTC),
        timezone_name="America/New_York",
    )
    assert result == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_patrol_source_fields_round_trip_domain():
    job = ScheduledJob(name="x", owner_user_id="u", source_type="patrol_pack", source_id="pack-1")
    assert ScheduledJob.model_validate(job.model_dump()).source_id == "pack-1"
