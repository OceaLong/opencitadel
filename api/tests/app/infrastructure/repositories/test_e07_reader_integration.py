"""Owned DB tests of E07 wiring: real fixed artifact pages and consumed replay evidence."""

# ruff: noqa: F401,F811
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.evaluation.test_rule_evaluator import held_rule_cleanup
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_score_repository import (
    completed,
    with_rules,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]
KERNEL = AuthorizationContext.system("execution-kernel")


async def tool_output(scope, row, handler, command, *, body=None, action=None):
    from app.domain.models.artifact_provenance import ArtifactProducer
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter

    activity = uuid4()
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity),
            "activity_type": "tool.call",
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "input_ref": "e07/test-input",
            "input_digest": "a" * 64,
        },
        2,
    )
    store = PostgresActivityStore(session_factory=handler._session_factory, authorization=KERNEL)
    claim = (
        await store.claim(
            now=datetime.now(UTC), limit=1, worker_id="e07-evidence", claim_ttl=timedelta(minutes=5)
        )
    )[0]
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    await command(
        "MarkActivityCallStarted",
        {"activity_id": str(activity), "generation": 0, "claim_generation": claim.claim_generation},
        2,
    )
    producer = ArtifactProducer(
        scope=scope,
        run_id=row["run_id"],
        activity_id=activity,
        generation=0,
        claim_generation=claim.claim_generation,
    )
    if action:
        await action(producer)
    command_id = uuid4()
    if body is not None:
        await ExecutionContentWriter(
            session_factory=handler._session_factory, authorization=KERNEL, objects=None
        ).record(
            producer,
            command_id=command_id,
            phase="output",
            value={
                "kind": "tool",
                "message": {"role": "tool", "name": "write_file", "content": json.dumps(body)},
            },
        )
    await command(
        "CompleteActivity",
        {
            "activity_id": str(activity),
            "generation": 0,
            "claim_generation": claim.claim_generation,
            "result_ref": "e07/tool/" + str(activity),
        },
        2,
        command_id,
    )
    return producer


@pytest.mark.parametrize("mode", ["complete", "digest_changed", "revoked_between_pages"])
async def test_real_fixed_artifact_pages_integrity_and_current_authority(
    budget_binding_fixture, mode
):
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.application.security.authorization_context import authorization_scope
    from app.application.services.artifact_service import ArtifactService
    from app.application.services.execution_content_service import ExecutionContentService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.repositories.postgres_artifact_upload_intents import (
        PostgresArtifactUploadIntentWriter,
    )

    fixture = await with_rules(
        budget_binding_fixture,
        [
            {
                "kind": "artifact",
                "artifact_kind": "doc",
                "schema": {"type": "string", "pattern": "^Fixed"},
            }
        ],
    )
    suites, scope, principal, _, _, _, factory = fixture
    objects = suites.datasets.objects
    lifecycle = suites.datasets.object_intents
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    session = str(uuid4())
    async with factory(KERNEL) as work:
        await work.db_session.execute(
            text("INSERT INTO sessions(id,owner_user_id,status) VALUES(:id,:owner,'completed')"),
            {"id": session, "owner": scope.user_id},
        )
        await work.commit()
    artifact_service = ArtifactService(
        lambda: factory(auth),
        objects,
        upload_intents=PostgresArtifactUploadIntentWriter(
            lifecycle.factory, signing_secret=lifecycle.secret
        ),
    )
    artifact = None
    content = "Fixed 🙂 unicode artifact\n" * 10000

    async def before_model(row, handler, command):
        async def write(producer):
            nonlocal artifact
            with authorization_scope(auth):
                artifact = await artifact_service.write_content(
                    session, None, "doc", "Fixed", content, verify_upload=False, producer=producer
                )

        await tool_output(scope, row, handler, command, body={"success": True}, action=write)
        result = await ArtifactProvenanceMaintenance(
            session_factory=handler._session_factory,
            authorization=KERNEL,
            objects=objects,
            handler=handler,
        ).process_pending()
        assert result["emitted"] == 1

    _, _, batch, candidate = await completed(
        fixture, final_output="answer", before_model=before_model
    )
    assert artifact is not None
    fixed_key = artifact.version_refs[0]
    # Append a distinct latest version after the terminal cut: E07 must still read v1.
    with authorization_scope(auth):
        await artifact_service.write_content(
            session, artifact.id, "doc", "Latest", "different latest", verify_upload=False
        )
    if mode == "digest_changed":
        objects.data[fixed_key] = b"changed immutable bytes"
    pages = []

    class ObservedContent(ExecutionContentService):
        async def read_artifact(self, *args, **kwargs):
            page = await super().read_artifact(*args, **kwargs)
            pages.append(page)
            if mode == "revoked_between_pages" and len(pages) == 1:
                async with factory(KERNEL) as work:
                    await work.db_session.execute(
                        text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                        {"id": principal.user_id},
                    )
                    await work.commit()
            return page

    reader = RuleEvidenceReader(
        factory,
        content_factory=lambda authorization: ObservedContent(
            lambda: factory(authorization),
            None,
            ArtifactService(lambda: factory(authorization), objects),
            cursor_secret=b"e07-fixed-artifact-pages-secret",
        ),
    )
    consumer = RuleScoringService(factory, suites, reader)
    if mode == "revoked_between_pages":
        with pytest.raises(PermissionError, match="revoked"):
            await consumer.score(scope, candidate)
        assert len(pages) == 1
        async with factory(KERNEL) as work:
            assert await work.evaluation_score.revision(scope, batch.id) == 0
        return
    await consumer.score(scope, candidate)
    async with factory(KERNEL) as work:
        score = (await work.evaluation_score.history(scope, batch.id, evaluation_revision=1))[
            0
        ].score
    if mode == "complete":
        assert len(pages) > 1
        assert "".join(p.content for p in pages) == content
        assert {p.version for p in pages} == {1}
        assert score.value is True
        assert any(
            e.resource_kind == "artifact"
            and e.resource_id == artifact.id
            and e.resource_version == "1"
            for e in score.evidence
        )
    else:
        assert score.status == "not_evaluable"
        assert score.value is None


async def test_real_consumer_times_out_one_rule_and_settles_next(budget_binding_fixture):
    from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    output = json.dumps("a" * 32 + "!")
    fixture = await with_rules(
        budget_binding_fixture,
        [
            {"kind": "json_schema", "schema": {"type": "string", "pattern": "^(a+)+$"}},
            {"kind": "text_exact", "expected": output},
        ],
    )
    suites, scope, _, _, _, _, factory = fixture
    _, _, batch, candidate = await completed(fixture, final_output=output)
    consumer = RuleScoringService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        evaluator=IsolatedRuleEvaluator(timeout_seconds=0.75),
    )
    await asyncio.wait_for(consumer.score(scope, candidate), 5)
    async with factory(KERNEL) as work:
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        assert history[0].score.status == "error"
        assert history[0].score.reason == "rule_evaluation_timeout"
        assert history[0].score.value is None
        assert history[1].score.value is True
        assert await work.evaluation_score.settled(scope, candidate.result_id, "rule") == "failed"
        assert (await work.evaluation_batch.results(scope, batch.id))[0][
            "scoring_status"
        ] == "pending"


async def with_write_recording(fixture, *, redacted_pin_phase=None):
    from app.domain.evaluation.configuration import ConfigSelection, SuiteDefinition
    from app.domain.evaluation.recording import (
        MatchRule,
        RecordedContract,
        RecordingJob,
        RecordingManifest,
        RecordingSlot,
        canonical,
        recording_key,
    )
    from app.domain.models.resource_pin import ResourceIdentity
    from app.infrastructure.repositories.db_evaluation_dataset_repository import params

    suites, scope, principal, suite, _, pair, factory = fixture
    draft = await suites.create(
        scope,
        principal,
        kind="config",
        name="Write replay",
        definition=ConfigSelection(model_id="e02-model", tool_names=("write_file",)).model_dump(
            mode="json"
        ),
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
    descriptor = config.snapshot["contracts"][0]
    contract = RecordedContract(
        name="write_file",
        pack=descriptor["pack"],
        schema_body=descriptor["schema"],
        policy=descriptor["policy"],
        binding_revision="1",
        authority_revision="1",
    )
    job = RecordingJob(id=uuid4(), source_run_id=uuid4())
    pins = ()
    if redacted_pin_phase is not None:
        assert redacted_pin_phase in {"input", "output"}
        content_id = uuid4()
        body = json.dumps({"request": {"secret": "[redacted]"}})
        digest = hashlib.sha256(body.encode()).hexdigest()
        async with factory(KERNEL) as work:
            await work.db_session.execute(
                text("""INSERT INTO execution_public_content
                  (content_id,command_id,run_id,activity_id,generation,claim_generation,phase,body,content_digest,byte_length,redacted,citation_refs,owner_user_id,team_id,created_by)
                  VALUES (:id,:command,:run,:activity,0,1,:phase,:body,:digest,:length,true,'[]'::jsonb,:owner,:team,'execution-worker')"""),
                params(
                    scope,
                    id=content_id,
                    command=uuid4(),
                    run=job.source_run_id,
                    activity=uuid4(),
                    phase=redacted_pin_phase,
                    body=body,
                    digest=digest,
                    length=len(body.encode()),
                ),
            )
            await work.db_session.execute(
                text("""INSERT INTO execution_content_bindings
                  (event_id,phase,content_id,run_id,step_id,formal_position,owner_user_id,team_id,created_by)
                  VALUES (:event,:phase,:id,:run,'source/model-content',1,:owner,:team,'execution-worker')"""),
                params(
                    scope,
                    event=uuid4(),
                    id=content_id,
                    run=job.source_run_id,
                    phase=redacted_pin_phase,
                ),
            )
            await work.commit()
        pins = (
            ResourceIdentity(
                resource_kind="execution_content",
                resource_id=str(content_id),
                resource_version=digest,
            ),
        )
    data = canonical({"success": True})
    object_id = uuid4()
    key = "e07/" + str(object_id)
    await suites.datasets.objects.put_bytes(key, data)
    slot = RecordingSlot(
        id=uuid4(),
        tool="write_file",
        contract_digest=contract.digest,
        match_key=recording_key(
            "write_file", contract.digest, {"filepath": "/test", "content": "recorded"}, "root", 0
        ),
        rule=MatchRule(),
        branch="root",
        ordinal=0,
        object_id=object_id,
        result_digest=hashlib.sha256(data).hexdigest(),
        result_bytes=len(data),
        simulated_effect=True,
    )
    manifest = RecordingManifest(
        id=uuid4(),
        job_id=job.id,
        source_run_id=job.source_run_id,
        catalog_fingerprint="fixed",
        contracts=(contract,),
        slots=(slot,),
        pins=pins,
    )
    async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        await work.evaluation_recording.create(scope, job, [], principal)
        token = await work.evaluation_recording.claim(scope, job.id)
        await work.db_session.execute(
            text(
                "INSERT INTO evaluation_recording_objects(id,job_id,storage_key,digest,size_bytes,owner_user_id,team_id,created_by) VALUES(:id,:job,:key,:digest,:size,:owner,:team,:actor)"
            ),
            params(
                scope, id=object_id, job=job.id, key=key, digest=slot.result_digest, size=len(data)
            ),
        )
        await work.evaluation_recording.publish(scope, manifest, token)
        await work.resource_pins.acquire(
            scope, "recording_version", str(manifest.id), manifest.pins
        )
        await work.commit()
    definition = SuiteDefinition(
        **{k: getattr(suite, k) for k in SuiteDefinition.model_fields}
    ).model_copy(update={"config_versions": (config.id,), "recording_versions": (manifest.id,)})
    draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="Consumed write evidence",
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
    return (suites, scope, principal, suite, config, pair, factory), manifest, slot


@pytest.mark.parametrize(
    "mode",
    ["corroborated", "redacted_input", "redacted_output", "missing_output", "wrong_revision"],
)
async def test_actual_reader_corroborates_durable_write_slot(budget_binding_fixture, mode):
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    fixture = await with_rules(
        budget_binding_fixture, [{"kind": "text_exact", "expected": "answer"}]
    )
    fixture, manifest, slot = await with_write_recording(
        fixture,
        redacted_pin_phase=(
            "input" if mode == "redacted_input" else "output" if mode == "redacted_output" else None
        ),
    )
    suites, scope, principal, _, _, _, factory = fixture
    producer = None

    async def before_model(row, handler, command):
        async def consume(producer):
            async with factory(KERNEL) as work:
                await work.evaluation_recording.consume(
                    scope, producer.run_id, producer.activity_id, manifest.id, slot
                )
                await work.commit()

        nonlocal producer
        body = (
            None
            if mode == "missing_output"
            else {
                "success": True,
                "simulated_effect": True,
                "recording_revision": (
                    manifest.revision
                    if mode in {"corroborated", "redacted_input", "redacted_output"}
                    else 999
                ),
            }
        )
        producer = await tool_output(scope, row, handler, command, body=body, action=consume)

    _, _, batch, candidate = await completed(
        fixture, final_output="answer", before_model=before_model
    )
    reader = RuleEvidenceReader(factory, content_factory=lambda auth: None)
    evidence = await reader.read(scope, principal, candidate, resources=())
    await RuleScoringService(factory, suites, reader).score(scope, candidate)
    async with factory(KERNEL) as work:
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        ledger = await work.evaluation_recording.consumed(
            scope, producer.run_id, producer.activity_id
        )
    assert ledger is not None
    score = history[0].score
    if mode in {"corroborated", "redacted_input", "redacted_output"}:
        assert evidence.evidence.simulated is True
        assert score.value is True
        assert score.recording.version_id == manifest.id
        assert score.recording.total == score.recording.consumed == 1
        assert score.recording.unused == 0
        assert score.recording.simulated_activity_ids == (producer.activity_id,)
        if mode in {"redacted_input", "redacted_output"}:
            assert len(manifest.pins) == 1
            assert manifest.pins[0] not in evidence.resources
    else:
        assert evidence.unavailable_reason == "recording_evidence_unavailable"
        assert evidence.evidence.simulated is False
        assert score.status == "not_evaluable"
        assert score.value is None


async def test_actual_consumer_cancellation_reaps_without_settlement(
    budget_binding_fixture, monkeypatch
):
    from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    fixture = await with_rules(
        budget_binding_fixture, [{"kind": "json_schema", "schema": {"pattern": "^(a+)+$"}}]
    )
    suites, scope, _, _, _, _, factory = fixture
    _, _, batch, candidate = await completed(fixture, final_output=json.dumps("a" * 40 + "!"))
    processes = []
    spawned = asyncio.Event()
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    consumer = RuleScoringService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        evaluator=IsolatedRuleEvaluator(timeout_seconds=5),
    )
    pending = asyncio.create_task(consumer.score(scope, candidate))
    await asyncio.wait_for(spawned.wait(), 3)
    await asyncio.sleep(0.3)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 2)
    assert processes[0].returncode is not None
    async with factory(KERNEL) as work:
        assert await work.evaluation_score.revision(scope, batch.id) == 0
        assert await work.evaluation_score.settled(scope, candidate.result_id, "rule") is None
    consumer.evaluator = IsolatedRuleEvaluator(timeout_seconds=0.75)
    await asyncio.wait_for(consumer.score(scope, candidate), 3)
    assert len(processes) == 2
    assert all(p.returncode is not None for p in processes)
    async with factory(KERNEL) as work:
        assert await work.evaluation_score.revision(scope, batch.id) == 1


async def test_actual_consumer_cancel_during_timeout_cleanup_stops_admission(
    budget_binding_fixture, held_rule_cleanup
):
    from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    output = json.dumps("a" * 40 + "!")
    fixture = await with_rules(
        budget_binding_fixture,
        [
            {"kind": "json_schema", "schema": {"pattern": "^(a+)+$"}},
            {"kind": "text_exact", "expected": output},
        ],
    )
    suites, scope, _, _, _, _, factory = fixture
    _, _, batch, candidate = await completed(fixture, final_output=output)
    processes, cleanup_entered, release = held_rule_cleanup
    consumer = RuleScoringService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        evaluator=IsolatedRuleEvaluator(timeout_seconds=0.1),
    )
    pending = asyncio.create_task(consumer.score(scope, candidate))
    try:
        await asyncio.wait_for(cleanup_entered.wait(), 3)
        for _ in range(3):
            pending.cancel()
            await asyncio.sleep(0)
        assert not pending.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 3)
    assert len(processes) == 1  # No admission of the next rule.
    assert processes[0].returncode is not None
    async with factory(KERNEL) as work:
        assert await work.evaluation_score.revision(scope, batch.id) == 0
        assert await work.evaluation_score.settled(scope, candidate.result_id, "rule") is None
        assert await work.evaluation_score.history(scope, batch.id, evaluation_revision=0) == ()
