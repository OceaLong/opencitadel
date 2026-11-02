# ruff: noqa: F401,F811 -- isolated database fixture dependencies
"""Definition head and request pins through real, non-bypass database roles."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.activity_worker import ActivityWorker
from app.application.execution.definition_registry import DefinitionRegistry
from app.application.execution.run_service import RunService
from app.domain.errors import ConflictError
from app.domain.execution.activity import ActivityOutcome
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.definition import (
    DefinitionIntegrityError,
    SchemaCompatibility,
    WorkerCapabilities,
)
from app.domain.execution.family import RunFamily
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.definition_models import admissions, definitions, heads, workers
from app.infrastructure.execution.models import ExecutionActivityTaskORM, ExecutionEventORM
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_definition_store import (
    DefinitionRetirementBlocked,
    PostgresDefinitionStore,
)
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.domain.execution.test_definition import manifest as value_manifest
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


def manifest(version=1, **changes):
    aggregate = RunAggregate()

    def schemas(registry):
        return tuple(
            SchemaCompatibility(
                name=name,
                minimum=1,
                maximum=registry.latest_version(name),
                write_version=registry.latest_version(name),
            )
            for name in sorted(registry.registered_names())
        )

    return value_manifest(
        version,
        command_schemas=schemas(aggregate.command_registry),
        event_schemas=schemas(aggregate.event_registry),
        **changes,
    )


@pytest.fixture
async def definition_runtime(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=isolated_database[0].url.database)
    )
    sessions = authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().database_authorization_signing_secret
    )
    try:
        yield service, scope, principal, sessions
    finally:
        await engine.dispose()


def capability(value):
    return WorkerCapabilities(
        worker_id="definitions-worker",
        boot_id="boot-1",
        build_artifact_digest="c" * 64,
        definitions=(value.pin,),
        handlers=value.handlers,
        compilers=(value.compiler_version,),
        command_schemas=value.command_schemas,
        event_schemas=value.event_schemas,
    )


async def test_v01_head_switch_keeps_original_request_pin_and_new_requests_take_v2(
    definition_runtime,
):
    service, scope, principal, sessions = definition_runtime
    one, two = manifest(1), manifest(2)
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        store = PostgresDefinitionStore(db)
        await store.seed_head(one)
        await store.register(two)
        await store.heartbeat(capability(two), ttl=timedelta(seconds=30))
        await db.commit()
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    command, run = uuid4(), uuid4()
    async with service.uow_factory(auth) as work:
        first = await PostgresDefinitionStore(work.db_session).pin_admission(
            family=RunFamily.AGENT,
            scope_key=f"user:{scope.user_id}",
            command_id=command,
            run_id=run,
        )
        assert first == one.pin
        await work.commit()
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        store = PostgresDefinitionStore(db)
        request = str(uuid4())
        assert (
            await store.publish_head(
                two.pin,
                expected_digest=one.digest,
                actor_user_id=principal.user_id,
                request_id=request,
                reason="Publish second controlled implementation",
            )
            == two.pin
        )
        assert (
            await store.publish_head(
                two.pin,
                expected_digest=one.digest,
                actor_user_id=principal.user_id,
                request_id=request,
                reason="Publish second controlled implementation",
            )
            == two.pin
        )
        await db.commit()
    async with service.uow_factory(auth) as work:
        store = PostgresDefinitionStore(work.db_session)
        assert (
            await store.pin_admission(
                family=RunFamily.AGENT,
                scope_key=f"user:{scope.user_id}",
                command_id=command,
                run_id=run,
            )
            == one.pin
        )
        assert (
            await store.pin_admission(
                family=RunFamily.AGENT,
                scope_key=f"user:{scope.user_id}",
                command_id=uuid4(),
                run_id=uuid4(),
            )
            == two.pin
        )
        await work.commit()


async def test_v04_database_manifest_identity_is_immutable_and_expired_worker_cannot_publish(
    definition_runtime,
):
    _, _scope, principal, sessions = definition_runtime
    one, two = manifest(), manifest(2)
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        store = PostgresDefinitionStore(db)
        await store.seed_head(one)
        await store.register(two)
        await store.heartbeat(capability(two), ttl=timedelta(seconds=30))
        await db.execute(
            update(workers).values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
        with pytest.raises(ConflictError, match="VERSION_UNAVAILABLE"):
            await store.publish_head(
                two.pin,
                expected_digest=one.digest,
                actor_user_id=principal.user_id,
                request_id=str(uuid4()),
                reason="Must require a live complete worker",
            )
        await db.commit()
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        with pytest.raises(DefinitionIntegrityError):
            await PostgresDefinitionStore(db).register(manifest(build_artifact_digest="d" * 64))
        await db.rollback()


async def test_pin_scope_and_immutable_tables_are_not_api_write_authority(definition_runtime):
    service, scope, principal, sessions = definition_runtime
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        await PostgresDefinitionStore(db).seed_head(manifest())
        await db.commit()
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    async with service.uow_factory(auth) as work:
        with pytest.raises(DBAPIError):
            await PostgresDefinitionStore(work.db_session).pin_admission(
                family=RunFamily.AGENT,
                scope_key=f"user:{uuid4()}",
                command_id=uuid4(),
                run_id=uuid4(),
            )
    async with service.uow_factory(auth) as work:
        with pytest.raises(DBAPIError):
            await work.db_session.execute(update(definitions).values(body={}))


async def test_v02_v03_unsupported_worker_does_not_claim_and_exact_worker_resumes(
    definition_runtime,
):
    _, _scope, principal, sessions = definition_runtime
    auth = AuthorizationContext.system("execution-kernel")
    one, two = manifest(1), manifest(2)
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=auth
    )
    run, activity = uuid4(), uuid4()
    from datetime import UTC, datetime

    async def command(name, payload, version=1):
        result = await orchestrator.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=name,
                command_schema_version=version,
                stream_type="run",
                stream_id=str(run),
                owner_user_id=principal.user_id,
                team_id=None,
                correlation_id=run,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )
        assert result.status == "accepted", result

    await command(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": "definition-owned-fixture",
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
            "definition_pin": one.pin.model_dump(mode="json"),
            "definition_manifest": one.model_dump(mode="json"),
        },
        3,
    )
    await command("StartRun", {})
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity),
            "activity_type": "tool.call",
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
            "input_digest": "a" * 64,
        },
        4,
    )
    await PostgresFormalProjector(session_factory=sessions, authorization=auth).run_once(
        OwnerScope.personal(principal.user_id), limit=100, notify=False
    )
    calls = []

    class VersionedHandler:
        activity_type = "tool.call"
        idempotent = True

        def __init__(self, version):
            self.version = version

        async def execute(self, request, context):
            calls.append(self.version)
            return ActivityOutcome.succeeded(
                result_ref=None, result_summary=f"implementation-{self.version}"
            )

    def planner(*args):
        return None

    def compiler(*args):
        return None

    def worker(value):
        local = DefinitionRegistry()
        local.register(value, planner=planner, compiler=compiler)
        handlers = ActivityRegistry()
        handlers.register(
            VersionedHandler(str(value.definition_version)),
            handler_version=str(value.definition_version),
        )
        return ActivityWorker(
            store=PostgresActivityStore(
                session_factory=sessions,
                authorization=auth,
                supported_implementations=frozenset(
                    {(value.digest, "tool.call", str(value.definition_version))}
                ),
            ),
            run_contexts=PostgresRunContextSource(session_factory=sessions, authorization=auth),
            run_service=RunService(orchestrator=orchestrator),
            registry=handlers,
            definitions=local,
            worker_id=f"version-{value.definition_version}",
        )

    assert (await worker(two).run_once(now=datetime.now(UTC), limit=1)).claimed == 0
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        row = await db.get(ExecutionActivityTaskORM, activity)
        assert row.claim_attempts == row.attempt == row.claim_generation == 0
        assert row.status == "pending"
    assert calls == []
    assert (await worker(one).run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
    assert calls == ["1"]


async def test_worker_boot_capability_is_immutable(definition_runtime):
    _, _scope, _principal, sessions = definition_runtime
    one, two = manifest(1), manifest(2)
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        store = PostgresDefinitionStore(db)
        await store.register(one)
        await store.register(two)
        await store.heartbeat(capability(one), ttl=timedelta(seconds=30))
        await store.heartbeat(capability(one), ttl=timedelta(seconds=30))
        with pytest.raises(DefinitionIntegrityError, match="boot identity"):
            await store.heartbeat(capability(two), ttl=timedelta(seconds=30))


async def test_v05_v06_active_definition_cannot_retire_and_cancel_needs_no_planner(
    definition_runtime,
):
    _, _scope, principal, sessions = definition_runtime
    from datetime import UTC, datetime

    one, two = manifest(1), manifest(2)
    auth = AuthorizationContext.system("execution-kernel")
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=auth
    )
    run_id = uuid4()

    async def command(name, payload, version=1):
        return await orchestrator.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=name,
                command_schema_version=version,
                stream_type="run",
                stream_id=str(run_id),
                owner_user_id=principal.user_id,
                team_id=None,
                correlation_id=run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )

    async with sessions() as db:
        await configure_session_authorization(db, auth)
        store = PostgresDefinitionStore(db)
        await store.seed_head(one)
        await store.register(two)
        await store.heartbeat(capability(two), ttl=timedelta(seconds=30))
        await store.publish_head(
            two.pin,
            expected_digest=one.digest,
            actor_user_id=principal.user_id,
            request_id=str(uuid4()),
            reason="test future head",
        )
        await db.commit()

    assert (
        await command(
            "CreateRun",
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "retirement-test",
                "semantic_payload": {},
                "policy_snapshot": run_policy_snapshot_json("agent"),
                "definition_pin": one.pin.model_dump(mode="json"),
                "definition_manifest": one.model_dump(mode="json"),
            },
            3,
        )
    ).status == "accepted"
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        store = PostgresDefinitionStore(db)
        with pytest.raises(DefinitionRetirementBlocked) as error:
            await store.retire(
                one.pin,
                actor_user_id=principal.user_id,
                request_id=str(uuid4()),
                reason="must remain until drained",
            )
        assert error.value.references["counts"]["active_runs"] == 1
        assert error.value.references["run_ids"] == [str(run_id)]
    from sqlalchemy import text

    from app.application.execution.decision_worker import DecisionWorker
    from app.infrastructure.execution.postgres_run_decision_source import PostgresRunDecisionSource

    projector = PostgresFormalProjector(session_factory=sessions, authorization=auth)
    await projector.run_once(OwnerScope.personal(principal.user_id), limit=100, notify=False)
    unavailable = DecisionWorker(
        source=PostgresRunDecisionSource(
            session_factory=sessions, authorization=auth, supported_definitions=frozenset()
        ),
        run_service=RunService(orchestrator=orchestrator),
        definitions=DefinitionRegistry(),
    )
    assert (await unavailable.run_once(now=datetime.now(UTC), limit=10)).loaded == 0
    # A cancel command remains available while the installed worker has no planner.
    assert (await command("CancelRun", {"reason": "development drain"})).status == "accepted"
    await projector.run_once(OwnerScope.personal(principal.user_id), limit=100, notify=False)
    assert (await unavailable.run_once(now=datetime.now(UTC), limit=10)).submitted == 0
    request = str(uuid4())
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        assert await db.scalar(text("SELECT count(*) FROM execution_model_dispatches")) == 0
        assert await db.scalar(text("SELECT count(*) FROM execution_activity_tasks")) == 0
        store = PostgresDefinitionStore(db)
        assert (
            await store.retire(
                one.pin, actor_user_id=principal.user_id, request_id=request, reason="drained"
            )
            == one.pin
        )
        assert (
            await store.retire(
                one.pin, actor_user_id=principal.user_id, request_id=request, reason="drained"
            )
            == one.pin
        )
        await db.commit()


async def test_context_prepare_real_worker_commits_one_durable_manifest(definition_runtime):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.application.execution.activities.context_prepare import ContextPrepareActivityHandler
    from app.application.execution.activity_inputs import ActivityObjectStore
    from app.application.execution.context_compiler import compile_context
    from app.application.ports.context_preparation import ContextCompilerInput
    from app.domain.execution.context_models import (
        ContextBoundary,
        ContextMessage,
        ContextPolicy,
        ContextSource,
        ContextSourceRef,
        context_digest,
    )
    from app.domain.execution.definition import HandlerImplementation
    from tests.app.application.execution.test_context_compiler import PROFILE
    from tests.app.application.execution.test_context_objects import Storage

    _, _scope, principal, sessions = definition_runtime
    run_id, activity_id = uuid4(), uuid4()
    scope = OwnerScope.personal(principal.user_id)
    storage = Storage()
    objects = ActivityObjectStore(storage)
    key, digest = await objects.put_input(run_id, {"message": "current task"})
    policy = ContextPolicy()
    value = manifest(
        context_policy=policy,
        handlers=(
            HandlerImplementation(
                activity_type=ContextPrepareActivityHandler.activity_type, handler_version="1"
            ),
        ),
    )
    local = DefinitionRegistry()
    local.register(value, planner=lambda *args: None, compiler=compile_context)
    message = ContextMessage(role="user", content="current task")
    inputs = ContextCompilerInput(
        pin=value.pin,
        boundary=ContextBoundary(
            scope_key=f"user:{principal.user_id}",
            collection_id=str(run_id),
            through_position=0,
            current_input_digest=digest,
        ),
        policy=policy,
        candidates=(PROFILE,),
        summary_candidates=(PROFILE,),
        sources=(
            ContextSource(
                ref=ContextSourceRef(
                    source_id=f"current:{digest}",
                    scope_key=f"user:{principal.user_id}",
                    run_id=run_id,
                    kind="user",
                    digest=digest,
                    formal_position=0,
                    authorization_epoch="fixture-current",
                ),
                messages=(message,),
                messages_digest=context_digest([message.model_dump(mode="json")]),
                protected=True,
            ),
        ),
    )

    async def materialize(*args, **kwargs):
        return inputs

    handlers = ActivityRegistry()
    handlers.register(
        ContextPrepareActivityHandler(
            objects=objects, definitions=local, preparation=SimpleNamespace(materialize=materialize)
        )
    )
    auth = AuthorizationContext.system("execution-kernel")
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=auth
    )

    async def command(name, payload, version=1):
        result = await orchestrator.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=name,
                command_schema_version=version,
                stream_type="run",
                stream_id=str(run_id),
                owner_user_id=principal.user_id,
                team_id=None,
                correlation_id=run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )
        assert result.status == "accepted"

    await command(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "prepare-owned-fixture",
            "source_entity_id": str(run_id),
            "semantic_payload": {"input_ref": key, "input_digest": digest},
            "policy_snapshot": run_policy_snapshot_json("agent"),
            "definition_pin": value.pin.model_dump(mode="json"),
            "definition_manifest": value.model_dump(mode="json"),
        },
        3,
    )
    await command("StartRun", {})
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity_id),
            "activity_type": ContextPrepareActivityHandler.activity_type,
            "input_ref": key,
            "input_digest": digest,
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
        },
        4,
    )
    await PostgresFormalProjector(session_factory=sessions, authorization=auth).run_once(
        scope, limit=100, notify=False
    )
    worker = ActivityWorker(
        store=PostgresActivityStore(
            session_factory=sessions,
            authorization=auth,
            supported_implementations=frozenset(
                {(value.digest, ContextPrepareActivityHandler.activity_type, "1")}
            ),
        ),
        run_contexts=PostgresRunContextSource(session_factory=sessions, authorization=auth),
        run_service=RunService(orchestrator=orchestrator),
        registry=handlers,
        definitions=local,
        worker_id="prepare-real-worker",
    )
    assert (await worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
    assert (await worker.run_once(now=datetime.now(UTC), limit=1)).claimed == 0
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        row = await db.get(ExecutionActivityTaskORM, activity_id)
        assert row.status == "succeeded"
        assert row.result_ref
        assert row.decision_payload["context_status"] == "ready"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ExecutionEventORM)
                .where(
                    ExecutionEventORM.stream_id == str(run_id),
                    ExecutionEventORM.event_type == "ActivityCompleted",
                )
            )
            == 1
        )


async def test_admission_service_pin_and_inbox_share_api_transaction_and_rollback(
    definition_runtime,
):
    from app.application.execution.activity_inputs import ActivityObjectStore
    from app.application.execution.admission import RunAdmissionService
    from app.application.execution.command_ingress import CommandIngress
    from app.infrastructure.execution.admission_definition_resolver import (
        AdmissionDefinitionResolver,
    )
    from app.infrastructure.execution.models import ExecutionCommandInboxORM
    from tests.app.application.execution.test_context_objects import Storage
    from tests.app.application.execution.test_policy_admission import (
        _active_execution,
        _PolicyHeads,
    )

    service, scope, principal, sessions = definition_runtime
    value = manifest()
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        await PostgresDefinitionStore(db).seed_head(value)
        await db.commit()
    admission = RunAdmissionService(
        command_ingress=CommandIngress(writer=None),
        activity_objects=ActivityObjectStore(Storage()),
        policy_heads=_PolicyHeads(_active_execution()),
        definition_resolver=AdmissionDefinitionResolver(),
    )
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    key = f"definition-transaction:{uuid4()}"

    async def admit(work):
        return await admission.admit(
            family=RunFamily.AGENT,
            source_entity_type="session",
            source_entity_id="atomic-pin-test",
            owner_scope=scope,
            private_input={"message": "hello", "_execution_definition": {"forged": True}},
            public_input={"message": "hello"},
            idempotency_key=key,
            command_sink=work.execution_commands,
            inference_read_context=work,
        )

    async with service.uow_factory(auth) as work:
        run_id = await admit(work)
        row = await work.db_session.scalar(
            select(ExecutionCommandInboxORM).where(
                ExecutionCommandInboxORM.stream_id == str(run_id)
            )
        )
        assert row.payload["definition_pin"] == value.pin.model_dump(mode="json")
        assert await work.db_session.scalar(select(func.count()).select_from(admissions)) == 1
        # No commit: the receipt and command must disappear together.
    async with service.uow_factory(auth) as work:
        assert await work.db_session.scalar(select(func.count()).select_from(admissions)) == 0
        assert (
            await work.db_session.scalar(select(func.count()).select_from(ExecutionCommandInboxORM))
            == 0
        )
        assert await admit(work) == run_id
        await work.commit()
    async with service.uow_factory(auth) as work:
        assert await work.db_session.scalar(select(func.count()).select_from(admissions)) == 1
        assert (
            await work.db_session.scalar(select(func.count()).select_from(ExecutionCommandInboxORM))
            == 1
        )


async def test_v05_cancelled_run_with_formal_unknown_effect_still_blocks_retirement(
    definition_runtime,
):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.application.execution.activity_inputs import ActivityObjectStore
    from app.application.execution.effect_service import EffectService, command_envelope
    from app.domain.execution.effect import EffectOperationSpec
    from app.infrastructure.execution.postgres_effect_store import PostgresEffectStore
    from tests.app.application.execution.test_context_objects import Storage

    dataset_service, scope, principal, sessions = definition_runtime
    auth = AuthorizationContext.system("execution-kernel")
    one, two = manifest(1), manifest(2)
    run_id, activity_id, operation_id = uuid4(), uuid4(), uuid4()
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=auth
    )

    async def command(name, payload, version=1, proof=None):
        envelope = CommandEnvelope(
            command_id=uuid4(),
            command_type=name,
            command_schema_version=version,
            stream_type="run",
            stream_id=str(run_id),
            owner_user_id=scope.user_id,
            team_id=None,
            correlation_id=run_id,
            causation_id=None,
            issued_at=datetime.now(UTC),
            payload=payload,
            activity_lease_proof=proof,
        )
        result = await orchestrator.handle(envelope)
        assert result.status == "accepted", result

    async with sessions() as db:
        await configure_session_authorization(db, auth)
        definitions = PostgresDefinitionStore(db)
        await definitions.seed_head(one)
        await definitions.register(two)
        await definitions.heartbeat(capability(two), ttl=timedelta(minutes=1))
        await definitions.publish_head(
            two.pin,
            expected_digest=one.digest,
            actor_user_id=principal.user_id,
            request_id=str(uuid4()),
            reason="Prepare retirement of the first definition",
        )
        await db.commit()
    await command(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "owned-unknown-fixture",
            "source_entity_id": str(run_id),
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
            "definition_pin": one.pin.model_dump(mode="json"),
            "definition_manifest": one.model_dump(mode="json"),
        },
        3,
    )
    await command("StartRun", {})
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity_id),
            "activity_type": "tool.call",
            "input_digest": "a" * 64,
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
        4,
    )
    activities = PostgresActivityStore(session_factory=sessions, authorization=auth)
    claim = (
        await activities.claim(
            now=datetime.now(UTC),
            limit=1,
            worker_id="unknown-retirement",
            claim_ttl=timedelta(minutes=2),
        )
    )[0]
    assert await activities.mark_call_started(claim, now=datetime.now(UTC))
    await command(
        "MarkActivityCallStarted",
        {
            "activity_id": str(activity_id),
            "generation": 0,
            "claim_generation": claim.claim_generation,
        },
        2,
        claim.lease_proof,
    )
    store = PostgresEffectStore(
        session_factory=sessions, authorization=auth, orchestrator=orchestrator
    )
    service = EffectService(store=store, objects=ActivityObjectStore(Storage()), files=None)
    spec = EffectOperationSpec(
        operation_id=operation_id,
        invocation_id=operation_id,
        run_id=run_id,
        activity_id=activity_id,
        generation=0,
        provider_identity="unknown-fixture",
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key=str(operation_id),
        capability="manual_only",
    )
    context = SimpleNamespace(
        worker_id=claim.worker_id,
        claim_generation=claim.claim_generation,
        run=SimpleNamespace(owner_scope=scope),
    )
    for name, payload in (
        ("PrepareEffectOperation", {"spec": spec.model_dump(mode="json")}),
        (
            "StartEffectDispatch",
            {
                "operation_id": str(operation_id),
                "expected_revision": 1,
                "dispatch_proof": claim.lease_proof.model_dump(mode="json"),
            },
        ),
    ):
        result = await store.worker_command(
            command_envelope(spec=spec, name=name, payload=payload, context=context)
        )
        assert result.status == "accepted", result
    unknown = await service.persist_observation(
        spec,
        state="unknown",
        source="transport_unknown",
        evidence={"operation_id": str(operation_id), "reason": "response lost after external send"},
    )
    assert (
        await store.worker_command(
            command_envelope(
                spec=spec,
                name="RecordEffectObservation",
                payload={
                    "operation_id": str(operation_id),
                    "expected_revision": 2,
                    "observation": unknown.model_dump(mode="json"),
                },
                context=context,
            )
        )
    ).status == "accepted"
    await command(
        "MarkActivityOutcomeUnknown",
        {
            "activity_id": str(activity_id),
            "generation": 0,
            "claim_generation": claim.claim_generation,
            "failure_code": "EXTERNAL_OUTCOME_UNKNOWN",
        },
        2,
        claim.lease_proof,
    )
    await command("CancelRun", {"reason": "Cancel without inventing a remote result"})
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        with pytest.raises(DefinitionRetirementBlocked) as blocked:
            await PostgresDefinitionStore(db).retire(
                one.pin,
                actor_user_id=principal.user_id,
                request_id=str(uuid4()),
                reason="Unknown operation must remain queryable",
            )
        assert blocked.value.references["counts"]["active_runs"] == 0
        assert blocked.value.references["counts"]["unsettled_effects"] == 1
        assert blocked.value.references["run_ids"] == [str(run_id)]

    # The public asynchronous management API must preserve this obligation too.
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.application.execution.definition_management import DefinitionManagementService
    from app.domain.models.scope import WorkspaceContext
    from app.domain.models.user import GlobalRole
    from app.infrastructure.execution.definition_management import PostgresDefinitionManagement
    from app.interfaces.auth_dependencies import get_workspace_context, require_admin
    from app.interfaces.endpoints.execution_definition_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_definition_management_service

    principal = principal.model_copy(update={"global_role": GlobalRole.ADMIN})
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    user = AuthorizationContext.for_principal(principal, scope=scope)
    ingress = PostgresDefinitionManagement(
        session_factory=dataset_service.uow_factory(user).session_factory, authorization=user
    )
    worker = PostgresDefinitionManagement(session_factory=sessions, authorization=auth)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=principal, scope=scope
    )
    app.dependency_overrides[get_definition_management_service] = lambda: (
        DefinitionManagementService(ingress, wait_seconds=0)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://owned-test"
    ) as client:
        path = "/admin/execution-definitions/agent/1/retire"
        body = {
            "request_id": str(uuid4()),
            "expected_manifest_digest": one.digest,
            "reason": "Do not retire an unresolved operation",
        }
        assert (await client.post(path, json=body)).status_code == 202
        assert (await worker.process_pending())["emitted"] == 1
        response = await client.post(path, json=body)
        assert response.status_code == 409, response.text
        references = response.json()["data"]["references"]
        assert references["counts"]["active_runs"] == 0
        assert references["counts"]["unsettled_effects"] == 1
        assert references["run_ids"] == [str(run_id)]


async def test_v01_approval_resumes_original_planner_and_handler_after_head_switch(
    definition_runtime,
):
    from datetime import UTC, datetime
    from functools import partial

    from redis.asyncio import Redis

    from app.application.execution.decisions.base import (
        ActivityStep,
        WorkflowPlan,
        next_plan_command,
    )
    from app.composition.kernel_runtime import build_execution_kernel_runtime
    from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader

    service, scope, principal, sessions = definition_runtime
    auth = AuthorizationContext.system("execution-kernel")
    one, two = manifest(1), manifest(2)
    definitions = DefinitionRegistry()
    handlers = ActivityRegistry()
    calls = []

    def planner(state, context, outcomes, now, *, version):
        return next_plan_command(
            state,
            WorkflowPlan(
                steps=(
                    ActivityStep(
                        activity_type="tool.call",
                        timeout_seconds=120,
                        input_ref=None,
                        input_digest="a" * 64,
                        input_payload={"implementation": version},
                        requires_approval=True,
                    ),
                )
            ),
            now=now,
            max_retries=0,
        )

    class Handler:
        activity_type = "tool.call"
        idempotent = True

        def __init__(self, version):
            self.version = version

        async def execute(self, request, context):
            assert request.input_payload["implementation"] == self.version
            assert context.run.definition_pin.definition_version == self.version
            calls.append((context.run.run_id, self.version, request.handler_version))
            return ActivityOutcome.succeeded(result_ref=None, result_summary="done")

    for value in (one, two):
        definitions.register(
            value,
            planner=partial(planner, version=value.definition_version),
            compiler=lambda *args: None,
        )
        handlers.register(
            Handler(value.definition_version), handler_version=str(value.definition_version)
        )
    redis = Redis()
    runtime = build_execution_kernel_runtime(
        session_factory=sessions,
        redis=redis,
        authorization=auth,
        activity_registry=handlers,
        definitions=definitions,
    )

    async def command(run, name, payload, version=1):
        result = await runtime.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=name,
                command_schema_version=version,
                stream_type="run",
                stream_id=str(run),
                owner_user_id=principal.user_id,
                team_id=None,
                correlation_id=run,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )
        assert result.status == "accepted", result

    async def admit():
        run = uuid4()
        async with service.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            pin = await PostgresDefinitionStore(work.db_session).pin_admission(
                family=RunFamily.AGENT,
                scope_key=f"user:{scope.user_id}",
                command_id=uuid4(),
                run_id=run,
            )
            await work.commit()
        value = one if pin == one.pin else two
        await command(
            run,
            "CreateRun",
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "approval-head-switch",
                "semantic_payload": {},
                "policy_snapshot": run_policy_snapshot_json("agent"),
                "definition_pin": pin.model_dump(mode="json"),
                "definition_manifest": value.model_dump(mode="json"),
            },
            3,
        )
        return run

    async def drive(run, expected):
        for _ in range(12):
            await runtime.run_projector_once(scope, limit=100)
            assert not (await runtime.run_decisions_once(limit=10)).errors
            assert not (await runtime.run_activities_once(limit=10)).failed
            async with sessions() as db:
                await configure_session_authorization(db, auth)
                state = (
                    await PostgresVerifiedRunLoader(db).load(
                        str(run),
                        owner_scope=(principal.user_id, None),
                    )
                ).replay.state
            if state.status == expected:
                return state
        pytest.fail(f"Run did not reach {expected}: {state.status}")

    try:
        async with sessions() as db:
            await configure_session_authorization(db, auth)
            await PostgresDefinitionStore(db).seed_head(one)
            await db.commit()
        old = await admit()
        waiting = await drive(old, "waiting")
        assert not calls
        async with sessions() as db:
            await configure_session_authorization(db, auth)
            store = PostgresDefinitionStore(db)
            await store.register(two)
            await store.heartbeat(capability(two), ttl=timedelta(seconds=60))
            await store.publish_head(
                two.pin,
                expected_digest=one.digest,
                actor_user_id=principal.user_id,
                request_id=str(uuid4()),
                reason="switch while old approval waits",
            )
            await db.commit()
        new = await admit()
        await command(
            old,
            "DecideApproval",
            {
                "approval_id": str(waiting.pending_approval_id),
                "decision": "approved",
                "actor_user_id": str(principal.user_id),
            },
            2,
        )
        assert (await drive(old, "completed")).definition_pin == one.pin
        waiting = await drive(new, "waiting")
        assert waiting.definition_pin == two.pin
        await command(
            new,
            "DecideApproval",
            {
                "approval_id": str(waiting.pending_approval_id),
                "decision": "approved",
                "actor_user_id": str(principal.user_id),
            },
            2,
        )
        assert (await drive(new, "completed")).definition_pin == two.pin
        assert calls == [(old, 1, "1"), (new, 2, "2")]
    finally:
        await redis.aclose()


async def test_production_publisher_registers_exact_boot_without_moving_heads(definition_runtime):
    from app.composition.definition_lifecycle import build_definition_publisher
    from app.composition.execution_definitions import build_code_definitions

    _, _, _, sessions = definition_runtime
    registry = build_code_definitions()
    publisher = build_definition_publisher(
        sessions=sessions, definitions=registry, worker_id="production-capability-regression"
    )
    await publisher.run_once()
    await publisher.run_once()
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        rows = (await db.execute(select(workers))).mappings().all()
        assert len(rows) == 1
        assert rows[0]["body"] == publisher.capabilities.model_dump(mode="json")
        assert rows[0]["expires_at"] > rows[0]["last_seen_at"]
        assert await db.scalar(select(func.count()).select_from(heads)) == 0
        store = PostgresDefinitionStore(db)
        for value in registry.manifests:
            assert await store.manifest(value.pin) == value
    reboot = build_definition_publisher(
        sessions=sessions, definitions=registry, worker_id="production-capability-regression"
    )
    assert reboot.capabilities.boot_id != publisher.capabilities.boot_id
    await reboot.run_once()
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        assert await db.scalar(select(func.count()).select_from(workers)) == 2


async def test_non_context_definition_does_not_require_chat_candidates(definition_runtime):
    from types import SimpleNamespace

    from app.composition.execution_definitions import build_code_definitions
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService

    _, scope, _, sessions = definition_runtime
    value = next(
        item for item in build_code_definitions().manifests if item.family == RunFamily.KB_INGEST
    )
    run_id = uuid4()
    service = DurableBudgetDispatchService(
        uow_factory=None, inventory=None, physical_policy=None, execution_policy=None
    )
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        store = PostgresDefinitionStore(db)
        await store.seed_head(value)
        await store.pin_admission(
            family=value.family,
            scope_key=f"user:{scope.user_id}",
            command_id=uuid4(),
            run_id=run_id,
        )
        # No model resolver, chat metadata, inventory, or execution policy is
        # available. Non-chat admission must still retain its exact definition.
        assert (
            await service.freeze_context_candidates(
                SimpleNamespace(db_session=db), scope, run_id, value.pin, {}, None
            )
            is None
        )


async def test_migration_seed_is_atomic_repeatable_and_preserves_changed_head(
    definition_runtime, isolated_database, monkeypatch
):
    from app.composition.execution_definitions import build_code_definitions
    from app.migrate_execution_definitions import seed_execution_definition_heads

    engine, _ = isolated_database
    monkeypatch.setattr(
        "app.migrate_execution_definitions.sqlalchemy_sync_migration_database_uri",
        lambda _: engine.url.render_as_string(hide_password=False),
    )
    settings = load_deployment_settings()
    registry = build_code_definitions()
    count = len(registry.manifests)
    _, _, _, sessions = definition_runtime
    assert await seed_execution_definition_heads(settings) == count
    assert await seed_execution_definition_heads(settings) == count
    original = registry.manifests[0]
    replacement = original.model_copy(update={"definition_version": 3})
    # A kernel fixture establishes a previously changed head. Runtime head
    # changes are separately exercised through current admin authorization.
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        await db.execute(
            definitions.insert().values(
                family=replacement.family.value,
                definition_version=3,
                manifest_digest=replacement.digest,
                body=replacement.model_dump(mode="json"),
            )
        )
        await db.execute(
            heads.update()
            .where(heads.c.family == replacement.family.value)
            .values(manifest_digest=replacement.digest)
        )
        await db.commit()
    assert await seed_execution_definition_heads(settings) == count
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        assert (
            await db.scalar(
                select(heads.c.manifest_digest).where(heads.c.family == replacement.family.value)
            )
            == replacement.digest
        )
    changed = DefinitionRegistry()
    for value in registry.manifests:
        changed.register(
            value.model_copy(update={"build_artifact_digest": "a" * 64}),
            planner=registry.resolve(value.pin).planner,
            compiler=registry.resolve(value.pin).compiler,
        )
    monkeypatch.setattr("app.migrate_execution_definitions.build_code_definitions", lambda: changed)
    with pytest.raises(DefinitionIntegrityError, match="different content"):
        await seed_execution_definition_heads(settings)
    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        assert await db.scalar(select(func.count()).select_from(definitions)) == count + 1
