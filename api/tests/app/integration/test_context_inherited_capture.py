# ruff: noqa: F401,F811 -- owned PostgreSQL fixture dependencies
"""Copied answers retain revocable resources inherited from earlier session Runs."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.domain.execution.family import RunFamily
from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
from app.domain.models.session import Session
from app.infrastructure.execution.postgres_context_sources import PostgresContextSources
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


@pytest.mark.parametrize("kind", ["skill", "file"])
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
            "attachment_evidence": True,
        }
    ],
    indirect=True,
)
async def test_historical_resources_revoke_later_copied_answers(compact_runtime, kind):
    r = compact_runtime

    async def drain():
        for _ in range(100):
            inbox = await r.kernel.run_inbox_once(limit=5)
            await r.kernel.run_projector_once(r.scope, limit=100)
            decision = await r.kernel.run_decisions_once(limit=5)
            activity = await r.kernel.run_activities_once(limit=5)
            assert not inbox.rejected
            assert not decision.errors
            assert not decision.rejected
            assert not activity.failed
            assert not activity.unknown
            if not inbox.accepted and not decision.submitted and not activity.claimed:
                return
        pytest.fail("context fixture did not drain")

    await drain()
    session = Session(owner_user_id=r.scope.user_id)
    async with r.api_uow() as work:
        await work.session.save(session)
        await work.commit()
    runs = []
    for turn in range(2):
        async with r.api_uow() as work:
            cut = await PostgresContextSources(work.db_session, signing_secret="owned-cut").freeze(
                r.scope, session.id, request_id=uuid4()
            )
            resource = (
                (
                    {"skill_id": "context-skill"}
                    if kind == "skill"
                    else {"attachments": [{"file_id": "context-document"}]}
                )
                if turn == 0
                else {}
            )
            runs.append(
                await r.admission.admit(
                    family=RunFamily.ASK,
                    source_entity_type="session",
                    source_entity_id=session.id,
                    owner_scope=r.scope,
                    private_input={
                        "message": "Recall ORD83 and its original constraint.",
                        "model_id": "e02-model",
                        "session_id": session.id,
                        "_context_boundary": cut.model_dump(mode="json"),
                        **resource,
                    },
                    public_input={},
                    command_sink=work.execution_commands,
                    inference_read_context=work,
                )
            )
            await work.commit()
        await drain()
    async with r.uow(r.auth) as work:
        rows = (
            (
                await work.db_session.execute(
                    text("""
SELECT c.content_id,c.content_digest,c.context_dependencies FROM execution_public_content c
JOIN execution_content_bindings b USING(content_id)
JOIN execution_activity_tasks t USING(activity_id)
WHERE c.run_id=:run AND c.phase='output' AND t.activity_type='model.call'
"""),
                    {"run": runs[-1]},
                )
            )
            .mappings()
            .all()
        )
        assert rows
        for row in rows:
            assert kind in {item["kind"] for item in row["context_dependencies"]}
    identities = [
        ResourceIdentity(
            resource_kind="execution_content",
            resource_id=str(row["content_id"]),
            resource_version=row["content_digest"],
        )
        for row in rows
    ]
    async with r.api_uow() as work:
        for identity in identities:
            await work.resource_pins.resolve(r.scope, identity)
    async with r.uow(r.auth) as work:
        await work.db_session.execute(
            text(
                "UPDATE skills SET enabled=false WHERE id='context-skill'"
                if kind == "skill"
                else "UPDATE files SET content_available=false WHERE id='context-document'"
            )
        )
        await work.commit()
    async with r.api_uow() as work:
        for identity in identities:
            with pytest.raises(ResourceUnavailable):
                await work.resource_pins.resolve(r.scope, identity)
