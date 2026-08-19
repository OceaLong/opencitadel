#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone

from app.domain.utils.time_utils import to_naive_utc


def test_aware_datetime_converted_to_naive_utc():
    aware = datetime(2026, 7, 19, 18, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    result = to_naive_utc(aware)
    assert result == datetime(2026, 7, 19, 10, 0, 0)
    assert result.tzinfo is None


def test_utc_aware_strips_tzinfo_without_shift():
    aware = datetime(2026, 7, 19, 10, 3, 7, tzinfo=timezone.utc)
    result = to_naive_utc(aware)
    assert result == datetime(2026, 7, 19, 10, 3, 7)
    assert result.tzinfo is None


def test_naive_datetime_passthrough():
    naive = datetime(2026, 7, 19, 10, 3, 7)
    assert to_naive_utc(naive) is naive


def test_none_passthrough():
    assert to_naive_utc(None) is None
