from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.execution.projection_recovery import rebuild_verified_scope
from app.domain.models.scope import OwnerScope


@pytest.mark.asyncio
async def test_recovery_only_clears_scope_after_replay_and_run_verification():
    calls = []

    async def mark(_):
        calls.append("mark")

    async def clear(_):
        calls.append("clear")

    async def replay(_):
        calls.append("replay")
        return "result"

    async def verify(_):
        calls.append("verify")
        return ("run",)

    marker = SimpleNamespace(mark=mark, clear=clear)
    projector = SimpleNamespace(rebuild=replay)
    source = SimpleNamespace(recover_scope=verify)
    result = await rebuild_verified_scope(
        OwnerScope.personal("u"), marker=marker, projector=projector, source=source
    )
    assert calls == ["mark", "replay", "verify", "clear"]
    assert result == ("result", ("run",))


@pytest.mark.asyncio
async def test_failed_verification_keeps_scope_quarantined():
    marker = SimpleNamespace(mark=AsyncMock(), clear=AsyncMock())
    projector = SimpleNamespace(rebuild=AsyncMock())
    source = SimpleNamespace(
        recover_scope=AsyncMock(side_effect=RuntimeError("database unavailable"))
    )
    with pytest.raises(RuntimeError):
        await rebuild_verified_scope(
            OwnerScope.personal("u"), marker=marker, projector=projector, source=source
        )
    marker.clear.assert_not_awaited()
