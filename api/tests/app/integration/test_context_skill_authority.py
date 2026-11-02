# ruff: noqa: F401,F811 -- owned PostgreSQL fixtures
"""Frozen selected instructions remain revocable at native dispatch and copied-content reads."""

import pytest
from sqlalchemy import text

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


@pytest.mark.parametrize("stage", ["model", "chunk"])
@pytest.mark.parametrize("change", ["none", "disable", "edit", "owner"])
@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "session_messages": 1,
            "capture_content": True,
            "skill_evidence": True,
            "context_policy": {"selected_sources": 8, "recent_units": 1},
            "provided_history": [
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"Fact {i}"}
                for i in range(30)
            ],
        }
    ],
    indirect=True,
)
async def test_selected_skill_is_frozen_and_rechecked_before_native_send(
    compact_runtime, stage, change
):
    r = compact_runtime
    target = None
    for _ in range(100):
        await r.kernel.run_projector_once(r.scope, limit=100)
        decision = await r.kernel.run_decisions_once(limit=1)
        assert not decision.errors
        async with r.uow(r.auth) as work:
            target = (
                (
                    await work.db_session.execute(
                        text(
                            "SELECT activity_id,request_payload FROM execution_activity_tasks WHERE aggregate_id=:run AND status='pending' AND activity_type=:type ORDER BY created_at LIMIT 1"
                        ),
                        {
                            "run": str(r.binding.run_id),
                            "type": "model.call" if stage == "model" else "context.compact",
                        },
                    )
                )
                .mappings()
                .first()
            )
        if target:
            break
        result = await r.kernel.run_activities_once(limit=1)
        assert not result.failed
        assert not result.unknown
    assert target
    sent = len(r.sends)
    async with r.uow(r.auth) as work:
        originals = (
            (
                await work.db_session.execute(
                    text(
                        'SELECT content_id,content_digest FROM execution_public_content WHERE run_id=:run AND context_dependencies @> \'[{"kind":"skill"}]\'::jsonb'
                    ),
                    {"run": r.binding.run_id},
                )
            )
            .mappings()
            .all()
        )
        assert originals
        if change != "none":
            sql = {
                "disable": "UPDATE skills SET enabled=false WHERE id='context-skill'",
                "edit": "UPDATE skills SET body='Renew instead' WHERE id='context-skill'",
                "owner": "UPDATE skills SET owner_user_id=NULL WHERE id='context-skill'",
            }[change]
            await work.db_session.execute(text(sql))
            await work.commit()
    result = await r.kernel.run_activities_once(limit=1)
    if change == "none":
        assert result.succeeded == 1
        assert len(r.sends) == sent + 1
        if stage == "model":
            system = r.responses.main[-1]["messages"][0]["content"]
            assert "Never renew ORD83; retain amount 7341." in system
            assert "Selected instructions survive compaction." in system
    else:
        assert result.failed == 1
        assert len(r.sends) == sent
        async with r.api_uow() as work:
            for row in originals:
                with pytest.raises(
                    ResourceUnavailable, match="copied context dependency unavailable"
                ):
                    await work.resource_pins.resolve(
                        r.scope,
                        ResourceIdentity(
                            resource_kind="execution_content",
                            resource_id=str(row["content_id"]),
                            resource_version=row["content_digest"],
                        ),
                    )
