# ruff: noqa: F401,F811 -- isolated database fixture dependencies
"""A copied memory fact and all later summaries remain revocable before sending."""

import pytest
from sqlalchemy import text

from app.domain.execution.context_models import ContextCompilation
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


@pytest.mark.parametrize("resource", ["memory", "knowledge"])
@pytest.mark.parametrize("stage", ["model", "chunk", "merge"])
@pytest.mark.parametrize("change", ["delete", "edit"])
@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 12,
            "memory_evidence": True,
            "knowledge_evidence": True,
            "context_policy": {"selected_sources": 8, "recent_units": 1},
        }
    ],
    indirect=True,
)
async def test_c08_resource_revocation_blocks_model_chunk_and_merge(
    compact_runtime, stage, change, resource
):
    runtime = compact_runtime
    target = None
    for _ in range(160):
        await runtime.kernel.run_projector_once(runtime.scope, limit=100)
        decisions = await runtime.kernel.run_decisions_once(limit=1)
        assert not decisions.errors
        assert not decisions.rejected
        async with runtime.uow(runtime.auth) as work:
            rows = (
                (
                    await work.db_session.execute(
                        text(
                            "SELECT activity_id,activity_type,request_payload FROM execution_activity_tasks "
                            "WHERE aggregate_id=:run AND status='pending' ORDER BY created_at"
                        ),
                        {"run": str(runtime.binding.run_id)},
                    )
                )
                .mappings()
                .all()
            )
        for row in rows:
            if stage == "model" and row["activity_type"] == "model.call":
                target = row
            elif stage != "model" and row["activity_type"] == "context.compact":
                body = await runtime.objects.load_context_document(
                    row["request_payload"]["prepare_ref"]
                )
                plan = ContextCompilation.model_validate(body["compilation"]).compaction
                chunk = next(
                    item
                    for item in plan.chunks
                    if item.ordinal == row["request_payload"]["ordinal"]
                )
                # Merges must actually consume a prior summary, not just a later raw chunk.
                derived = any('"facts"' in message.content for message in chunk.messages[1:])
                if (stage == "merge") == derived:
                    target = row
            if target is not None:
                break
        if target is not None:
            break
        activities = await runtime.kernel.run_activities_once(limit=1)
        assert not activities.failed
        assert not activities.stale
        assert not activities.unknown
    assert target is not None, stage
    sent_before = len(runtime.sends)
    async with runtime.uow(runtime.auth) as work:
        # The completed prepare and prior summaries remain intact. Only live source authority changes.
        sql = (
            "DELETE FROM memory_entries WHERE id='physical-context-memory'"
            if change == "delete"
            else "UPDATE memory_entries SET content='renew ORD83' WHERE id='physical-context-memory'"
        )
        if resource == "knowledge":
            sql = (
                "UPDATE knowledge_bases SET deleted_at=now() WHERE id='ctx-kb'"
                if change == "delete"
                else "UPDATE knowledge_chunks SET page_no=2 WHERE id='ctx-chunk'"
            )
        assert (await work.db_session.execute(text(sql))).rowcount == 1
        await work.commit()
    outcome = await runtime.kernel.run_activities_once(limit=1)
    assert outcome.failed == 1
    assert len(runtime.sends) == sent_before
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == sent_before
        )
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                runtime.binding.run_id, owner_scope=(runtime.scope.user_id, None)
            )
        ).replay.state
        assert (target["activity_id"], "failed", 0) in state.settled_activities
        assert await work.db_session.scalar(
            text("SELECT failure_code FROM execution_activity_tasks WHERE activity_id=:activity"),
            {"activity": target["activity_id"]},
        ) == (
            "CONTEXT_RESOURCE_AUTHORIZATION_REVOKED"
            if change == "delete"
            else "CONTEXT_RESOURCE_VERSION_CHANGED"
        )
