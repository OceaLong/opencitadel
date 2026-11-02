# ruff: noqa: F401,F811 -- isolated database fixture dependencies
"""Captured original and derived bodies cannot outlive current source authority."""

import json

import pytest
from sqlalchemy import text

from app.domain.execution.context_models import context_digest
from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
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
            "iterations": 12,
            "memory_evidence": True,
            "capture_content": True,
            "context_policy": {"selected_sources": 8, "recent_units": 1},
        }
    ],
    indirect=True,
)
async def test_copied_memory_and_derived_summaries_revoke_content_and_recording_pins(
    compact_runtime,
):
    r = compact_runtime
    for _ in range(300):
        await r.kernel.run_projector_once(r.scope, limit=100)
        decisions = await r.kernel.run_decisions_once(limit=1)
        activities = await r.kernel.run_activities_once(limit=1)
        assert not decisions.errors
        assert not activities.failed
        if not decisions.submitted and not activities.claimed:
            break
    assert r.responses.summary
    assert len(r.responses.main) == 13
    await r.kernel.run_projector_once(r.scope, limit=100)
    async with r.uow(r.auth) as work:
        rows = (
            (
                await work.db_session.execute(
                    text("""
SELECT c.*,b.step_id,b.formal_position,t.activity_type FROM execution_public_content c
 JOIN execution_content_bindings b USING(content_id)
 JOIN execution_activity_tasks t USING(activity_id)
 WHERE c.run_id=:run AND jsonb_array_length(c.context_dependencies)>0
"""),
                    {"run": r.binding.run_id},
                )
            )
            .mappings()
            .all()
        )
    assert {row["activity_type"] for row in rows} >= {
        "retrieval.search",
        "context.prepare",
        "context.compact",
        "model.call",
    }
    exact_calls = set()
    for row in rows:
        if row["phase"] == "output" and row["activity_type"] in {"context.compact", "model.call"}:
            physical = json.loads(row["body"])["_context_dispatches"]
            for call in physical["calls"]:
                assert call["native_payload_exact"]
                assert call["request_snapshot"]["native_payload_digest"] in {
                    context_digest(wire) for wire in r.sends
                }
                assert call["configuration_id"]
                assert call["settlement"]["call_identity"] == call["call_identity"]
                exact_calls.add(call["call_identity"])
    assert len(exact_calls) == len(r.sends)
    async with r.api_uow() as work:
        for row in rows:
            assert await work.execution_content.get_snapshot(
                r.scope,
                str(row["content_id"]),
                r.binding.run_id,
                row["step_id"],
                row["formal_position"],
            )
            await work.resource_pins.resolve(
                r.scope,
                ResourceIdentity(
                    resource_kind="execution_content",
                    resource_id=str(row["content_id"]),
                    resource_version=row["content_digest"],
                ),
            )
    async with r.uow(r.auth) as work:
        await work.db_session.execute(
            text("DELETE FROM memory_entries WHERE id='physical-context-memory'")
        )
        await work.commit()
    async with r.api_uow() as work:
        for row in rows:
            # Both metadata and body reads fail; a pin/recording cannot restore access.
            for include_body in (False, True):
                with pytest.raises(ResourceUnavailable, match="copied memory unavailable"):
                    await work.execution_content.get_snapshot(
                        r.scope,
                        str(row["content_id"]),
                        r.binding.run_id,
                        row["step_id"],
                        row["formal_position"],
                        include_body=include_body,
                    )
            with pytest.raises(ResourceUnavailable, match="copied memory unavailable"):
                await work.resource_pins.resolve(
                    r.scope,
                    ResourceIdentity(
                        resource_kind="execution_content",
                        resource_id=str(row["content_id"]),
                        resource_version=row["content_digest"],
                    ),
                )
