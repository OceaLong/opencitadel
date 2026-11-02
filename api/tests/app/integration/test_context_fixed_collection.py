# ruff: noqa: F401,F811 -- actual isolated database and runtime fixture dependencies
"""Accepted non-session history flows through real prepare/summary/read-source permits."""

import json
from datetime import UTC, datetime

import pytest

from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from tests.app.infrastructure.repositories.test_context_physical_dispatch import (
    budget_binding_fixture,
    compact_runtime,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    object_sessions,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]

FIRST = "EARLY-PROVIDED amount=5749; never renew."
HISTORY = [
    {
        "role": "user" if index % 2 == 0 else "assistant",
        "content": FIRST if index == 0 else f"record {index}: exact evidence " * 16,
    }
    for index in range(150)
]


@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "read_collection": True,
            "provided_history": HISTORY,
        }
    ],
    indirect=True,
)
async def test_150_fixed_messages_survive_compaction_and_original_read(compact_runtime):
    runtime = compact_runtime
    for _ in range(60):
        await runtime.kernel.run_projector_once(runtime.scope, limit=100)
        decisions = await runtime.kernel.run_decisions_once(now=datetime.now(UTC), limit=1)
        assert not decisions.errors
        assert not decisions.rejected
        activities = await runtime.kernel.run_activities_once(now=datetime.now(UTC), limit=1)
        assert not activities.failed
        assert not activities.unknown
        assert not activities.stale
        assert not activities.deferred
        if decisions.submitted == 0 and activities.claimed == 0:
            break
    async with runtime.uow(runtime.auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                runtime.binding.run_id, owner_scope=(runtime.scope.user_id, None)
            )
        ).replay.state
    assert state.status.value == "completed"
    assert runtime.responses.summary
    assert len(runtime.responses.main) == 2
    final = runtime.responses.main[-1]["messages"]
    restored = next(
        json.loads(message["content"])
        for message in final
        if message.get("tool_call_id") == "read-0-1"
    )
    assert restored["data"]["text"] == FIRST
    assert not runtime.responses.main[-1].get("tools")
    assert max(item["read_bytes"] for item in runtime.preparation_reads) <= 8388608
