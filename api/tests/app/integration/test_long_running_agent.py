# ruff: noqa: F401,F811 -- real PostgreSQL fixture dependencies
"""A01: actual admission, code planner, Worker and native HTTP send protocol."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

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


@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "read_original": True,
        },
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 200,
            "code_definition": True,
            "read_early_tool": True,
        },
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 12,
            "code_definition": True,
        },
    ],
    indirect=True,
    ids=["short", "200-rounds", "production-definition"],
)
async def test_a01_tool_rounds_complete_before_tools_disabled_final_answer(
    compact_runtime, request
):
    runtime = compact_runtime
    iterations = runtime.responses.rounds
    assert runtime.policy.revision.policy.agent.max_iterations == iterations
    kernel = runtime.kernel
    for tick in range(iterations * 8 + 100):
        await kernel.run_projector_once(runtime.scope, limit=100)
        decisions = await kernel.run_decisions_once(now=datetime.now(UTC), limit=1)
        assert not decisions.errors, decisions
        assert not decisions.rejected, decisions
        activities = await kernel.run_activities_once(now=datetime.now(UTC), limit=1)
        assert activities.failed == 0, activities
        diagnosis = (tick, activities, runtime.pack.calls[-6:])
        assert not activities.unknown, diagnosis
        assert not activities.stale, diagnosis
        assert not activities.deferred, diagnosis
        if decisions.submitted == 0 and activities.claimed == 0:
            break
        if tick % 40 == 0:
            print(
                f"tick={tick} model_rounds={len(runtime.responses.main)} tool_results={len(runtime.pack.calls)} summaries={len(runtime.responses.summary)}",
                flush=True,
            )
    async with runtime.uow(runtime.auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                runtime.binding.run_id,
                owner_scope=(runtime.scope.user_id, None),
            )
        ).replay.state
        assert state.status.value == "completed", state.model_dump(mode="json")
        assert state.policy_snapshot.execution_revision_id == runtime.policy.revision.id
        assert await work.db_session.scalar(
            text("SELECT count(*) FROM execution_model_dispatches")
        ) == len(runtime.sends)
    assert len(runtime.responses.main) == iterations + 1
    assert runtime.pack.calls == [
        (ordinal, item)
        for ordinal in range(iterations)
        for item in range(2)
        if not (
            runtime.responses.original_source
            and ordinal == runtime.responses.original_round
            and item == 1
        )
    ]
    assert sum(kind == "tool.call" for _, kind, _ in state.requested_activities) == iterations * 2
    if runtime.responses.original_source:
        tool_messages = [
            message
            for message in runtime.responses.main[-1]["messages"]
            if message["role"] == "tool"
        ]
        recovered = [
            json.loads(message["content"])
            for message in tool_messages
            if message.get("tool_call_id") == f"read-{runtime.responses.original_round}-1"
        ]
        assert len(recovered) == 1
        original = recovered[0]["data"]["text"]
        if iterations == 200:
            evidence = json.loads(original)
            assert evidence["data"]["round"] == 0
            assert evidence["data"]["item"] == 0
            assert evidence["data"]["fact"] == 0
            # The early result is absent from the final visible history, but remains readable.
            assert all(message.get("tool_call_id") != "read-0-0" for message in tool_messages)
        else:
            assert original == "retain exact facts"
    assert not runtime.responses.main[-1].get("tools")
    if iterations == 200:
        assert len(state.activity_results) > 64
        assert runtime.responses.summary
    assert all(limit <= 262144 for _, limit in runtime.storage.reads)
    assert all(item["read_bytes"] <= 8388608 for item in runtime.preparation_reads)
    request.node.user_properties.append(
        ("raw_preparation_samples", json.dumps(runtime.preparation_reads))
    )
    request.node.user_properties.extend(
        [
            ("tool_rounds", iterations),
            ("main_model_calls", len(runtime.responses.main)),
            (
                "prepare_read_max_bytes",
                max(item["read_bytes"] for item in runtime.preparation_reads),
            ),
        ]
    )
    print(
        f"prepare_read_max={max(item['read_bytes'] for item in runtime.preparation_reads)}",
        flush=True,
    )
