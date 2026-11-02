# ruff: noqa: F401,F811 -- real PostgreSQL fixture dependencies
"""B01: session HTTP admission and Worker completions, never prefilled history."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.application.execution.command_ingress import CommandIngress
from app.application.execution.public_projection import PublicEventCursor
from app.application.services.agent_service import AgentService
from app.application.services.session_service import SessionService
from app.composition.context_runtime import context_admission_sources
from app.domain.execution.context_models import ContextCompilation
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import WorkspaceContext
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_context_sources import PostgresContextSources
from app.infrastructure.execution.postgres_public_projection import PostgresPublicProjection
from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.endpoints.session_routes import router
from app.interfaces.service_dependencies import (
    get_agent_service,
    get_audit_service,
    get_session_service,
)
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
            "session_messages": 2,
        },
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "session_messages": 150,
            "code_definition": True,
        },
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "session_messages": 3,
            "purge_between_turns": True,
        },
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "session_messages": 12,
            "memory_evidence": True,
            "memory_first_turn": True,
            "context_policy": {"selected_sources": 8, "recent_units": 1},
            "purge_between_turns": True,
        },
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "session_messages": 12,
            "attachment_evidence": True,
            "attachment_first_turn": True,
            "context_policy": {"selected_sources": 8, "recent_units": 1},
            "purge_between_turns": True,
        },
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "session_messages": 2,
            "code_definition": True,
        },
    ],
    indirect=True,
    ids=[
        "short",
        "150-turns",
        "queue-retention",
        "summary-original-evidence",
        "summary-original-attachment",
        "production-definition",
    ],
)
async def test_b01_one_hundred_fifty_session_admissions_and_answers_keep_formal_history(
    compact_runtime,
    request,
):
    runtime = compact_runtime
    turns = runtime.session_messages
    authorization = AuthorizationContext.for_principal(runtime.principal, scope=runtime.scope)
    sessions = runtime.api_uow().session_factory
    projection = PostgresRunProjection(session_factory=sessions, authorization=authorization)

    async def hint(*_args, **_kwargs):
        pass

    session_service = SessionService(
        uow_factory=runtime.api_uow,
        sandbox_factory=None,
        run_projection=projection,
        session_list_publisher=SimpleNamespace(publish_changed=hint),
    )
    agent = AgentService(
        uow_factory=runtime.api_uow,
        admission_service=runtime.admission,
        command_ingress=CommandIngress(writer=None),
        run_projection=projection,
        public_projection=PostgresPublicProjection(
            session_factory=sessions,
            authorization=authorization,
            cursor=PublicEventCursor(secret=b"owned-long-session"),
        ),
        context_sources_factory=context_admission_sources(signing_secret="owned-cut"),
        poll_interval_seconds=0.01,
        idle_timeout_seconds=120,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=runtime.principal, scope=runtime.scope
    )
    app.dependency_overrides[require_non_auditor] = lambda: runtime.principal
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_agent_service] = lambda: agent
    app.dependency_overrides[get_audit_service] = lambda: SimpleNamespace(record=hint)
    stopped = asyncio.Event()

    async def drive():
        while not stopped.is_set():
            inbox = await runtime.kernel.run_inbox_once(limit=5)
            assert not inbox.rejected, inbox
            await runtime.kernel.run_projector_once(runtime.scope, limit=100)
            decisions = await runtime.kernel.run_decisions_once(limit=5)
            assert not decisions.errors, decisions
            assert not decisions.rejected, decisions
            activities = await runtime.kernel.run_activities_once(limit=5)
            if activities.failed:
                async with runtime.uow(runtime.auth) as work:
                    failures = (
                        await work.db_session.execute(
                            text(
                                "SELECT activity_type,failure_code FROM execution_activity_tasks "
                                "WHERE failure_code IS NOT NULL ORDER BY updated_at DESC LIMIT 5"
                            )
                        )
                    ).all()
                pytest.fail(f"model_calls={len(runtime.responses.main)} failures={failures}")
            assert not activities.failed, activities
            assert not activities.unknown, activities
            assert not activities.stale, activities
            assert not activities.deferred, activities
            await asyncio.sleep(0.005)

    async with asyncio.TaskGroup() as group:
        group.create_task(drive())
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://owned-session"
            ) as client:
                created = await client.post(
                    "/sessions", json={"mode": "ask", "model_id": "e02-model"}
                )
                assert created.status_code == 200, created.text
                session_id = created.json()["data"]["session_id"]
                for turn in range(turns):
                    message = f"Fact {turn}: approved amount={turn * 17}. Keep the earlier constraints and cite the original source when asked."
                    if turn == 0:
                        message += " ARCHIVE-Z19 amount=7341; do not renew."
                    elif turn == turns - 1:
                        message += " Recall ARCHIVE-Z19 exact amount and prohibition."
                        if runtime.memory_first_turn or runtime.attachment_first_turn:
                            message += " What was the exact ORD83 amount and renewal constraint?"
                    response = await asyncio.wait_for(
                        client.post(
                            f"/sessions/{session_id}/chat",
                            json={
                                "request_id": str(uuid4()),
                                "message": message,
                                "attachments": ["context-document"]
                                if runtime.attachment_first_turn and turn == 0
                                else [],
                            },
                        ),
                        timeout=120,
                    )
                    assert response.status_code == 200, response.text
                    assert "event: done" in response.text, response.text[-2000:]
                    if runtime.purge_between_turns:
                        purged = await PostgresActivityStore(
                            session_factory=runtime.sessions,
                            authorization=runtime.auth,
                        ).purge_completed(
                            before=datetime.now(UTC) + timedelta(seconds=1), limit=1000
                        )
                        assert purged > 0
                    if turn % 25 == 0:
                        print(
                            f"session_admissions={turn + 1} model_calls={len(runtime.responses.main)} summaries={len(runtime.responses.summary)}",
                            flush=True,
                        )
        finally:
            stopped.set()
    async with runtime.uow(runtime.auth) as work:
        rows = (
            (
                await work.db_session.execute(
                    text("""
SELECT event_id,stream_id,internal_payload FROM execution_events WHERE event_type='RunCreated'
AND public_payload->>'source_entity_type'='session' AND public_payload->>'source_entity_id'=:session
ORDER BY position
"""),
                    {"session": session_id},
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == turns
        inputs = [
            await runtime.objects.load_input_bounded(
                key=row["internal_payload"]["semantic_payload"]["input_ref"],
                expected_digest=row["internal_payload"]["semantic_payload"]["input_digest"],
            )
            for row in rows
        ]
        assert all(not value["conversation"] and value["_context_boundary"] for value in inputs)
        assert (
            inputs[-1]["_context_boundary"]["through_position"]
            > inputs[0]["_context_boundary"]["through_position"]
        )
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM execution_run_projection WHERE source_entity_type='session' AND source_entity_id=:session AND terminal"
                ),
                {"session": session_id},
            )
            == turns
        )
        last = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                rows[-1]["stream_id"], owner_scope=(runtime.scope.user_id, None)
            )
        ).replay.state
        preparations = {
            identity
            for identity, kind, _generation in last.requested_activities
            if kind == "context.prepare"
        }
        prepared = next(
            ref
            for identity, _generation, ref, _summary, _digest in reversed(last.activity_results)
            if identity in preparations
        )
        document = await runtime.objects.load_context_document(prepared)
        compilation = ContextCompilation.model_validate(document["compilation"])
        assert compilation.status == "ready"
        assert f"formal:{rows[0]['event_id']}" in {
            source.source_id for source in compilation.manifest.original_closure
        }
        assert any(
            f"Fact {turns - 1}:" in message.content for message in compilation.manifest.messages
        )
        assert any(
            "ARCHIVE-Z19 amount=7341; do not renew." in message.content
            and f"formal:{rows[0]['event_id']}" in message.content
            for message in compilation.manifest.messages
        )
        lookups = document["original_lookups"]
        assert len(lookups) == 1
        assert lookups[0]["query"] == inputs[-1]["message"]
        # In addition to every formal question/answer, index original retrieval
        # and tool evidence retained through answer and checkpoint closures.
        assert lookups[0]["indexed_sources"] >= 2 * (turns - 1)
        assert f"formal:{rows[0]['event_id']}" in {
            match["source"]["source_id"] for match in lookups[0]["matches"]
        }
        if runtime.memory_first_turn:
            assert runtime.responses.summary
            matches = [
                match
                for match in lookups[0]["matches"]
                if match["source"]["kind"] == "knowledge"
                and match["source"]["run_id"] == rows[0]["stream_id"]
            ]
            assert matches, (
                "The first Run's original retrieval must survive summary and queue purge"
            )
            assert any(
                "ORD83 must not be renewed; exact amount=5749" in message.content
                and matches[0]["source"]["source_id"] in message.content
                for message in compilation.manifest.messages
            )
        if runtime.attachment_first_turn:
            assert runtime.responses.summary
            attachment_id = f"attachment:{rows[0]['stream_id']}:context-document"
            assert any(
                match["source"]["source_id"] == attachment_id for match in lookups[0]["matches"]
            )
            assert any(
                "ORD83: exact amount 7341; never renew." in message.content
                and attachment_id in message.content
                for message in compilation.manifest.messages
            )
    assert len(runtime.responses.main) == turns + 1  # one standalone fixture Run plus session Runs
    if runtime.purge_between_turns:
        # The last manifest must remain verifiable after all terminal queues are gone.
        await runtime.source_validator.validate(
            compilation.manifest.boundary, compilation.manifest.original_closure
        )
    if runtime.attachment_first_turn:
        async with runtime.uow(runtime.auth) as work:
            await work.db_session.execute(
                text("UPDATE files SET content_available=false WHERE id='context-document'")
            )
            await work.commit()
        with pytest.raises(ValueError, match="CONTEXT_RESOURCE_AUTHORIZATION_REVOKED"):
            await runtime.source_validator.validate(
                compilation.manifest.boundary, compilation.manifest.original_closure
            )
    if turns == 150:
        assert runtime.responses.summary
    assert all(not wire.get("tools") for wire in runtime.responses.main)
    assert all(item["read_bytes"] <= 8388608 for item in runtime.preparation_reads)
    request.node.user_properties.append(
        ("raw_preparation_samples", json.dumps(runtime.preparation_reads))
    )
    request.node.user_properties.extend(
        [
            ("session_turns", turns),
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
