"""Capture-slot contention retries remain bounded and preserve authority errors."""

from unittest.mock import AsyncMock, call

import pytest
from sqlalchemy.exc import DBAPIError

from app.infrastructure.repositories import db_execution_analysis_repository as module


@pytest.mark.asyncio
@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
async def test_capture_retries_serialization_with_bounded_backoff(monkeypatch, sqlstate):
    class ConcurrentUpdate(Exception):
        pass

    failure = ConcurrentUpdate("concurrent capture slot")
    failure.sqlstate = sqlstate
    repository = module.DBExecutionAnalysisRepository(None, signing_secret="test")
    result = object()
    repository._capture_once = AsyncMock(
        side_effect=[DBAPIError("SELECT", {}, failure)] * 4 + [result]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    monkeypatch.setattr(module.random, "uniform", lambda _low, _high: 1.0)

    assert await repository.capture(None, None, None, None) is result
    assert repository._capture_once.await_count == 5
    assert sleep.await_args_list == [call(0.05), call(0.1), call(0.2), call(0.4)]


@pytest.mark.asyncio
async def test_capture_does_not_retry_authority_revocation(monkeypatch):
    repository = module.DBExecutionAnalysisRepository(None, signing_secret="test")
    repository._capture_once = AsyncMock(side_effect=ValueError("analysis_refresh_required"))
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)

    with pytest.raises(ValueError, match="analysis_refresh_required"):
        await repository.capture(None, None, None, None)
    repository._capture_once.assert_awaited_once()
    sleep.assert_not_awaited()
