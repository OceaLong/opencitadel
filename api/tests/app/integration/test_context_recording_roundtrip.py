# ruff: noqa: F401,F811 -- owned PostgreSQL fixtures
"""Generate summary slots from real captured Activities and current source authority."""

import json
from uuid import uuid4

import pytest

from app.application.evaluation.context_recording import SUMMARY_RECORDING_TOOL
from app.application.evaluation.recording_service import RecordingService
from app.application.evaluation.recording_source import RecordingSource
from app.application.evaluation.recording_worker import RecordingWorker
from app.application.services.execution_content_service import ExecutionContentService
from app.application.services.execution_view_service import ExecutionViewService
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
from app.infrastructure.repositories.recording_object_lifecycle import RecordingObjectLifecycle
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
            "session_messages": 1,
            "capture_content": True,
            "capture_contracts": True,
            "replay_runtime": True,
            "memory_evidence": True,
            "context_policy": {"selected_sources": 8, "recent_units": 1},
            "provided_history": [
                {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": f"Original {index}: amount=7341, do not renew.",
                }
                for index in range(30)
            ],
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize(
    "mode", ["success", "changed_fact", "revoked_source", "lost_outcome", "corrupt_summary"]
)
async def test_summary_recording_keeps_native_calls_and_original_pins(
    compact_runtime, datasets, budget_binding_fixture, mode
):
    r = compact_runtime
    for _ in range(100):
        await r.kernel.run_projector_once(r.scope, limit=100)
        decisions = await r.kernel.run_decisions_once(limit=1)
        activities = await r.kernel.run_activities_once(limit=1)
        assert not decisions.errors
        assert not activities.failed
        assert not activities.unknown
        if not decisions.submitted and not activities.claimed:
            break
    assert r.responses.summary
    await r.kernel.run_projector_once(r.scope, limit=100)
    ds = datasets[0]
    auth = AuthorizationContext.for_principal(r.principal, scope=r.scope)
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=r.api_uow().session_factory, authorization=auth),
        cursor_secret=b"owned-summary-recording-views",
    )
    content = ExecutionContentService(
        r.api_uow, views, None, cursor_secret=b"owned-summary-recording-content"
    )
    service = RecordingService(ds.uow_factory, source=RecordingSource(content, views))
    job = await service.create(
        r.scope,
        r.principal,
        r.binding.run_id,
        [
            {
                "tool": "__retrieval__",
                "allowed_fields": ["query", "sources"],
                "argument_replacements": {"query": "retain exact facts"},
            }
        ],
        request_id=str(uuid4()),
    )
    lifecycle = RecordingObjectLifecycle(
        ds.object_intents.factory,
        r.objects._storage,
        signing_secret=ds.object_intents.secret,
    )
    await RecordingWorker(service, lifecycle).generate(r.scope, r.principal, job.id)
    status = await service.status(r.scope, r.principal, job.id)
    assert status.status == "ready", status
    from app.application.evaluation.recording_authority import validate_recording

    async with ds.uow_factory(auth) as work:
        manifest = await validate_recording(work, r.scope, r.principal, status.result_version)
    slots = [slot for slot in manifest.slots if slot.tool == SUMMARY_RECORDING_TOOL]
    assert len(slots) == len(r.responses.summary)
    assert len(manifest.pins) >= 2 * len(slots)
    async with r.api_uow() as work:
        for slot in slots:
            row = await work.evaluation_recording.object(r.scope, slot.object_id)
            body = json.loads(await r.objects._storage.get_bytes(row["storage_key"]))
            assert body["summary"]["facts"]
            assert body["request"]["messages"]
            assert body["chunk_digest"]
            assert body["compaction_digest"]
            assert all(
                call["native_payload_exact"] and call["settlement"]
                for call in body["physical"]["calls"]
            )
            assert slot.source_parent_step_id
    await replay_new_run(r, budget_binding_fixture, manifest, mode=mode, source=service.source)


async def replay_new_run(r, fixture, manifest, *, mode, source):
    from sqlalchemy import text

    from app.application.evaluation.budget_admission import (
        prepare_budget_binding,
        prepare_budget_namespace,
    )
    from app.application.evaluation.replay_admission import prepare_replay_binding
    from app.domain.evaluation.budget_binding import BudgetBindingSelection
    from app.domain.evaluation.configuration import ConfigSelection, SuiteDefinition
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.evaluation.rubric import RubricDefinition
    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot
    from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader

    suites, scope, principal, original_suite, _, _, uow = fixture
    draft = await suites.create(
        scope,
        principal,
        kind="config",
        name="Summary replay",
        definition=ConfigSelection(
            model_id="e02-model",
            mode="ask",
            tool_names=(),
            max_output_tokens=8192,
        ).model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    config = await suites.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    judge_draft = await suites.create(
        scope,
        principal,
        kind="config",
        name="Replay judge",
        definition=ConfigSelection(
            model_id="e02-model", mode="ask", purpose="evaluation_judge"
        ).model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    judge = await suites.publish(
        scope,
        principal,
        kind="config",
        entity_id=judge_draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    rubric_draft = await suites.create(
        scope,
        principal,
        kind="rubric",
        name="Replay rubric",
        definition=RubricDefinition(judge_config_version=judge.id).model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    rubric = await suites.publish(
        scope,
        principal,
        kind="rubric",
        entity_id=rubric_draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    definition = SuiteDefinition(
        dataset_version=original_suite.dataset_version,
        config_versions=(config.id,),
        rubric_version=rubric.id,
        mode="recorded",
        recording_versions=(manifest.id,),
        settings=original_suite.settings,
    )
    draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="Summary replay",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    suite = await suites.publish(
        scope,
        principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    pair = await suites.policies.load_active_pair()
    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.ASK)
    run_id, source_id = uuid4(), str(uuid4())
    async with uow() as work:
        namespace = await prepare_budget_namespace(
            work,
            suites,
            scope,
            principal,
            namespace_id=uuid4(),
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        await prepare_replay_binding(
            work,
            suites,
            scope,
            principal,
            run_id=run_id,
            source_entity_id=source_id,
            config_version_id=config.id,
            recording_version_id=manifest.id,
            policy_pair=pair,
            policy_snapshot=snapshot,
        )
        binding = await prepare_budget_binding(
            work,
            suites,
            scope,
            principal,
            selection=BudgetBindingSelection(
                namespace_id=namespace.id,
                run_id=run_id,
                source_entity_id=source_id,
                case_id=namespace.case_ids[0],
                config_version_id=config.id,
                subject_config_version_id=config.id,
                repeat=1,
            ),
            policy_pair=pair,
            policy_snapshot=snapshot,
        )
        await work.evaluation_execution.prepare(scope, run_id, ExecutionSlotPolicy(revision=1))
        await r.admission.admit(
            family=RunFamily.ASK,
            source_entity_type=binding.source_entity_type,
            source_entity_id=source_id,
            owner_scope=scope,
            run_id=run_id,
            private_input={
                "message": "retain exact facts",
                "model_id": "e02-model",
                "conversation": [
                    {
                        "role": "user" if index % 2 == 0 else "assistant",
                        "content": f"Original {index}: amount={9999 if mode == 'changed_fact' and index == 0 else 7341}, do not renew.",
                    }
                    for index in range(30)
                ],
            },
            public_input={},
            usage_purpose="evaluation_subject",
            command_sink=work.execution_commands,
            inference_read_context=work,
        )
        await work.commit()
    sent = len(r.sends)
    summaries = len(r.responses.summary)
    if mode == "corrupt_summary":
        slot = next(slot for slot in manifest.slots if slot.tool == SUMMARY_RECORDING_TOOL)
        async with uow(r.auth) as work:
            row = await work.evaluation_recording.object(scope, slot.object_id)
        r.objects._storage.objects[row["storage_key"]] = b'{"facts":[]}'
    if mode == "revoked_source":
        async with uow(r.auth) as work:
            await work.db_session.execute(
                text("DELETE FROM memory_entries WHERE id='physical-context-memory'")
            )
            await work.commit()
    read_start = len(r.objects._storage.reads)
    lost = False
    delivered = await r.kernel.run_inbox_once(limit=1)
    assert delivered.accepted == 1
    for _ in range(100):
        await r.kernel.run_projector_once(scope, limit=100)
        decisions = await r.kernel.run_decisions_once(limit=1)
        if mode == "lost_outcome" and not lost:
            async with uow(r.auth) as work:
                pending = await work.db_session.scalar(
                    text(
                        "SELECT count(*) FROM execution_activity_tasks WHERE aggregate_id=:run AND activity_type='context.compact' AND status='pending'"
                    ),
                    {"run": str(run_id)},
                )
            if pending:
                await lose_summary_outcome(r, run_id)
                lost = True
        activities = await r.kernel.run_activities_once(limit=1)
        assert not decisions.errors
        if mode in {"success", "lost_outcome"}:
            assert not activities.failed
        assert not activities.unknown
        if not decisions.submitted and not activities.claimed:
            break
    assert len(r.responses.summary) == summaries
    succeeded = mode in {"success", "lost_outcome"}
    assert len(r.sends) == sent + int(succeeded)  # Only a successful main answer sends.
    async with uow(r.auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                str(run_id),
                owner_scope=(scope.user_id, None),
            )
        ).replay.state
        assert state.status == ("completed" if succeeded else "failed"), state
        consumed = await work.db_session.scalar(
            text("SELECT count(*) FROM evaluation_replay_ledger WHERE run_id=:run"), {"run": run_id}
        )
        assert consumed == (
            len(manifest.slots) if succeeded else (0 if mode == "revoked_source" else 1)
        )
    if mode == "lost_outcome":
        assert lost
    if mode == "revoked_source":
        assert not any("recording" in key for key, _ in r.objects._storage.reads[read_start:])
    if succeeded:
        await r.kernel.run_projector_once(scope, limit=100)
        at, steps = await source.steps(scope, run_id)
        captured = []
        for step in steps:
            if step.activity_id is None or step.status != "completed":
                continue
            output, _, _ = await source.complete(scope, run_id, step.step_id, at, "output")
            if output.get("kind") != "context_compact":
                continue
            physical = output["_context_dispatches"]
            assert physical["calls"] == []
            assert physical["replay"]["version_id"] == str(manifest.id)
            assert physical["replay"]["source_step_id"]
            captured.append(physical["replay"]["slot_id"])
        assert set(captured) == {
            str(slot.id) for slot in manifest.slots if slot.tool == SUMMARY_RECORDING_TOOL
        }


async def lose_summary_outcome(r, run_id):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.application.execution.run_context import run_execution_context
    from app.domain.execution.activity import ActivityContext
    from app.domain.execution.commands import CommandEnvelope
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader

    store = PostgresActivityStore(session_factory=r.sessions, authorization=r.auth)
    claim = (
        await store.claim(
            now=datetime.now(UTC),
            limit=1,
            worker_id="lost-replay-outcome",
            claim_ttl=timedelta(minutes=1),
        )
    )[0]
    assert claim.request.activity_type == "context.compact"
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    async with r.uow(r.auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                str(run_id), owner_scope=(r.scope.user_id, None)
            )
        ).replay.state
    result = await r.kernel.handle(
        CommandEnvelope(
            command_id=uuid4(),
            stream_type="run",
            stream_id=str(run_id),
            command_type="MarkActivityCallStarted",
            command_schema_version=2,
            expected_stream_version=state.stream_version,
            owner_user_id=r.scope.user_id,
            team_id=None,
            issued_at=datetime.now(UTC),
            correlation_id=state.correlation_id,
            causation_id=None,
            payload={
                "activity_id": str(claim.request.activity_id),
                "generation": claim.request.generation,
                "claim_generation": claim.claim_generation,
            },
            activity_lease_proof=claim.lease_proof,
        )
    )
    assert result.status == "accepted", result
    context = ActivityContext(
        activity_id=claim.request.activity_id,
        generation=claim.request.generation,
        worker_id=claim.worker_id,
        claim_generation=claim.claim_generation,
        idempotency_key=str(claim.request.activity_id),
        owner_user_id=r.scope.user_id,
        team_id=None,
        run=run_execution_context(state),
    )
    handler = r.registry.resolve("context.compact")
    outcome = await handler.execute(claim.request, context)
    assert outcome.status == "succeeded"
    assert await handler.recovery_safe(claim.request, context.run)
    # Crash after ledger consumption/output persistence, before CompleteActivity.
    async with r.uow(r.auth) as work:
        await work.db_session.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=clock_timestamp()-INTERVAL '1 second' WHERE activity_id=:id"
            ),
            {"id": claim.request.activity_id},
        )
        await work.commit()
