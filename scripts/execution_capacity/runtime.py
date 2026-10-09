"""Actual historical driver. Only the explicit child entry point opens services.

This builds ordinary production services without starting their scheduling loops.
It drives real admitted retrieval work; no synthetic ActivityOutcome is supplied.
"""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from scripts.execution_capacity.commands import RunPlan
from scripts.execution_capacity.observers import (
    ObservedContent,
    ObservedHandler,
    ObservedSink,
    ObservedStorage,
)
from scripts.execution_capacity.persistence import PersistedFacts
from scripts.seed_execution_visualization import event_count, run_identity, run_started_at
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.application.execution.activity_worker import ActivityWorker
from app.application.execution.outbox_dispatcher import OutboxDispatcher
from app.application.execution.run_service import RunService
from app.application.security.authorization_context import authorization_scope
from app.composition.evaluation_execution import configured_execution_policy
from app.composition.execution_content import build_execution_view_service
from app.composition.kernel import _build_activity_registry
from app.composition.shared import build_shared_services
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate, RunFamily
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot
from app.infrastructure.adapters.execution_ports import (
    SqlAlchemyCommandEnvelopeWriter,
    SqlAlchemyOutboxStore,
)
from app.infrastructure.adapters.redis_capabilities import RedisWakeupAdapter
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_activity_timeout import PostgresActivityTimeoutGuard
from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.postgres_progress_sink import PostgresActivityProgressSink
from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator

KERNEL = AuthorizationContext.system("execution-kernel")


def runtime_request(command, semantic, policy, now):
    """Retain planned identity/version while using actual admitted I/O and policy."""
    return CommandEnvelope.model_validate(
        {
            **command.model_dump(mode="python"),
            "issued_at": now,
            "payload": {
                **command.payload,
                "timeout_at": (
                    now + timedelta(seconds=policy.common.activity.tool_timeout_seconds)
                ).isoformat(),
                "input_ref": semantic["input_ref"],
                "input_digest": semantic["input_digest"],
                "input_payload": {},
            },
        }
    )


class FencedClaims:
    def __init__(self, delegate, facts, expected):
        self.delegate, self.facts, self.expected = delegate, facts, expected

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def claim(self, **kwargs):
        await self.facts.assert_exclusive()
        before = await self.facts.task(self.expected)
        if before is None or before["status"] != "pending" or before["claim_generation"] != 0:
            raise ValueError("activity cannot be safely claimed again")
        claims = await self.delegate.claim(**kwargs)
        if len(claims) != 1 or claims[0].request.activity_id != self.expected:
            raise ValueError("unexpected actual global claim; retained for recovery")
        claim = claims[0]
        self.facts.journal.intent(
            "claim", f"{self.expected}:{claim.claim_generation}", claim.model_dump(mode="json")
        )
        return claims


class HistoricalHandler(ObservedHandler):
    def __init__(self, delegate, journal, facts, clock, start):
        super().__init__(delegate, journal)
        self.facts, self.clock, self.start = facts, clock, start

    async def handle(self, command):
        events = await self.facts.events(command.stream_id)
        accepted = [event for event in events if event.causation_id == command.command_id]
        if accepted:
            if self.journal.get("command", command.command_id) is None:
                raise ValueError("accepted source lacks original private envelope")
            self.record(command)
            from app.application.execution.orchestrator import CommandResult

            result = CommandResult(
                command_id=command.command_id,
                status="accepted",
                first_event_position=accepted[0].position,
                last_event_position=accepted[-1].position,
                rejection_code=None,
            )
            self.journal.acknowledge("command", command.command_id, result.model_dump(mode="json"))
            return result
        self.clock[0] = self.start + timedelta(milliseconds=len(events))
        return await super().handle(command)


async def verify_prerequisite(shared, facts, binding):
    """Operator binding is corroborated by current persisted authority."""
    async with shared.uow_factory(KERNEL) as work:
        user = await work.user.get_by_id(binding["principal_id"])
        if user is None or not user.is_active or user.id != facts.scope.user_id:
            raise ValueError("bootstrap principal unavailable")
        principal = Principal(
            user_id=user.id, global_role=user.global_role, token_version=user.token_version
        )
    authorized = AuthorizationContext.for_principal(principal, scope=facts.scope)
    async with shared.uow_factory(authorized) as work:
        session = await work.session.get_by_id(binding["session_id"], scope=facts.scope)
        if (
            session is None
            or session.owner_user_id != principal.user_id
            or session.team_id is not None
            or session.created_at.isoformat() != binding["session_created_at"]
            or session.mode.value != "ask"
            or session.latest_message
            or session.latest_message_at
            or session.files
            or session.resource_bindings
            or session.skill_id
            or session.sandbox_id
            or session.active_execution_run_id
            or session.active_execution_request_id
            or session.status.value != "pending"
        ):
            raise ValueError("bootstrap Ask session is foreign, changed or nonempty")
        if session.model_id != binding["model_id"]:
            raise ValueError("bootstrap model differs")
        if await work.memory_entry.recall_for_session(session.id, limit=1):
            raise ValueError("bootstrap session has memory")
    async with facts.session() as session:
        if await session.scalar(text("SELECT current_database()")) != binding["database_name"]:
            raise ValueError("database binding differs")
        if (
            str(await session.scalar(text("SELECT system_identifier FROM pg_control_system()")))
            != binding["database_system_identifier"]
        ):
            raise ValueError("actual database cluster identity differs")
        if (await session.scalars(text("SELECT version_num FROM alembic_version"))).all() != [
            binding["migration"]
        ]:
            raise ValueError("migration binding differs")
        if await session.scalar(
            text(
                "SELECT count(*) FROM execution_run_projection WHERE source_entity_type='session' AND source_entity_id=:id"
            ),
            {"id": binding["session_id"]},
        ):
            raise ValueError("bootstrap session has execution history")
    with authorization_scope(authorized):
        if (await shared.mcp_integration_service.resolve_mcp_runtime(facts.scope)).servers:
            raise ValueError("MCP runtime must be empty")
        if (await shared.a2a_integration_service.resolve_a2a_runtime(facts.scope)).servers:
            raise ValueError("A2A runtime must be empty")
        model = await shared.inference_model_service.resolve_chat(
            binding["model_id"], scope=facts.scope
        )
        if model.endpoint.id != binding["endpoint_id"]:
            raise ValueError("actual model endpoint differs")
    active = await shared.runtime_policy_reader.active_execution(
        require_fresh=True, now=datetime.now(UTC)
    )
    policy = derive_run_policy_snapshot(active, RunFamily.ASK)
    if (
        policy.family_policy.memory.vector_enabled
        or str(active.revision.id) != binding["policy_revision"]
    ):
        raise ValueError("actual empty-source memory policy differs")
    return authorized, policy


async def project(projector, scope):
    for _ in range(100):
        result = await projector.run_once(scope, limit=1000, notify=False)
        if not result.processed:
            return
    raise ValueError("actual projection did not converge")


async def historical(
    resources, supervisor, journal, binding, manifest, *, host_fence, _probe=False
):
    """Build all standard historical runs; returns only after persisted parity."""
    settings = resources.settings
    if (
        settings.env != "test"
        or settings.sandbox_labels.get("com.opencitadel.acceptance.run") != binding["invocation"]
    ):
        raise ValueError("explicit dedicated deployment required")
    if journal.get("invalid", manifest["fixture_id"]) is not None:
        raise ValueError("invalidated standard fixture cannot be resumed as ready")
    shared = build_shared_services(
        resources,
        supervisor=supervisor,
        object_storage_wrapper=lambda real: ObservedStorage(real, journal),
    )
    await shared.runtime_policy_reader.initialize()
    scope = OwnerScope.personal(binding["principal_id"])
    facts = PersistedFacts(resources.postgres.session_factory, KERNEL, journal, scope, host_fence)
    await facts.baseline()
    authorized, policy = await verify_prerequisite(shared, facts, binding)
    registry = _build_activity_registry(shared)
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )

    execution_policy = configured_execution_policy(settings)
    async with facts.session() as session:
        if await DBEvaluationExecutionRepository(session).active_policy() != execution_policy:
            raise ValueError("preprovisioned execution policy differs")
    gate = EvaluationExecutionGuard(
        execution_policy, session_factory=facts.sessions, authorization=KERNEL
    )
    projector = PostgresFormalProjector(session_factory=facts.sessions, authorization=KERNEL)
    views = build_execution_view_service(
        settings=settings, resources=resources, authorization=authorized
    )
    writer = SqlAlchemyCommandEnvelopeWriter(
        session_factory=facts.sessions, authorization=authorized
    )
    sink = ObservedSink(writer, journal)
    content = ObservedContent(
        ExecutionContentWriter(
            session_factory=facts.sessions, authorization=KERNEL, objects=shared.activity_objects
        ),
        journal,
    )
    fixture_id, seed = UUID(manifest["fixture_id"]), manifest["seed"]
    end = datetime.fromisoformat(manifest["window_end"])
    total = 0
    outbox = OutboxDispatcher(
        store=SqlAlchemyOutboxStore(session_factory=facts.sessions, authorization=KERNEL),
        publisher=RedisWakeupAdapter(resources.general_redis),
        delivery_errors=(OSError, RuntimeError, ValueError, SQLAlchemyError),
    )
    try:
        for index in range(1 if _probe else 100_000):
            await facts.assert_exclusive()
            # Revalidate current policy, source session, integrations and model
            # each Run. Host containment prevents concurrent producer mutation.
            current_authorized, current_policy = await verify_prerequisite(shared, facts, binding)
            if current_authorized != authorized:
                raise ValueError("current bootstrap authority changed")
            if current_policy != policy:
                raise ValueError("admission prerequisites drifted")
            if _probe:
                from scripts.execution_capacity.probe import ProbePlan

                plan = ProbePlan(fixture_id, seed, end, scope.user_id, policy)
                run_id, start, expected_events, expected_steps = plan.run_id, end, 30_003, 10_000
            else:
                plan = RunPlan(
                    fixture_id,
                    seed,
                    index,
                    end,
                    scope.user_id,
                    None,
                    policy,
                    "retrieval.search",
                    {},
                )
                run_id, start = run_identity(fixture_id, seed, index), run_started_at(index, end)
                expected_events = event_count(index)
                expected_steps = 3331 if index < 10 else 31 if index < 1000 else 32
            journal.intent(
                "run",
                run_id,
                {
                    "scope": facts.scope_key,
                    "index": index,
                    "fixture_id": str(fixture_id),
                    "seed": seed,
                },
            )
            clock = [start]
            actual = SqlAlchemyExecutionOrchestrator(
                session_factory=facts.sessions,
                aggregates={"run": RunAggregate()},
                authorization=KERNEL,
                activity_timeout=PostgresActivityTimeoutGuard(registry),
                evaluation_execution=gate,
                formal_now=lambda clock=clock: clock[0],
            )
            handler = HistoricalHandler(actual, journal, facts, clock, start)
            admission_id = uuid5(
                NAMESPACE_URL, f"opencitadel:admit:capacity:{fixture_id}:{seed}:{index}"
            )
            prior = await facts.command(admission_id, run_id)
            captured = journal.get("command", admission_id)
            if prior or captured:
                envelope = prior[0] if prior else CommandEnvelope.model_validate(captured["body"])
            else:
                # Configuration and object children are recoverable by this exact
                # durable parent even if admit never returns.
                with authorization_scope(authorized):
                    async with shared.uow_factory(authorized) as work:
                        await shared.run_admission_service.admit(
                            family=RunFamily.ASK,
                            source_entity_type="capacity_fixture",
                            source_entity_id=str(fixture_id),
                            owner_scope=scope,
                            run_id=run_id,
                            private_input={
                                "session_id": binding["session_id"],
                                "mode": "ask",
                                "model_id": binding["model_id"],
                                "message": "Controlled historical capacity retrieval",
                            },
                            public_input={"message": "Controlled historical capacity retrieval"},
                            idempotency_key=f"capacity:{fixture_id}:{seed}:{index}",
                            command_sink=sink,
                            inference_read_context=work,
                        )
                        await work.commit()
                envelope = CommandEnvelope.model_validate(journal.parent("command", admission_id))
            if (await handler.handle(envelope)).status != "accepted":
                raise ValueError("real admission not accepted")
            if envelope.payload["policy_snapshot"] != policy.model_dump(mode="json"):
                raise ValueError("admitted policy differs from verified pinned policy")
            configuration = await facts.configuration(
                run_id, settings.database_authorization_signing_secret
            )
            semantic = envelope.payload["semantic_payload"]
            payload = await shared.activity_objects.load_input(
                key=semantic["input_ref"], expected_digest=semantic["input_digest"]
            )
            if payload["_execution_usage"]["configuration_id"] != configuration:
                raise ValueError("input/configuration binding differs")
            for planned in plan.commands():
                if planned.command_type in {
                    "CreateRun",
                    "MarkActivityCallStarted",
                    "CompleteActivity",
                }:
                    continue  # normal worker owns its deterministic command IDs
                command = planned.envelope
                persisted = await facts.command(command.command_id, run_id)
                saved = journal.get("command", command.command_id)
                if persisted or saved:
                    command = (
                        persisted[0] if persisted else CommandEnvelope.model_validate(saved["body"])
                    )
                elif planned.command_type == "RequestActivity":
                    command = runtime_request(command, semantic, policy, datetime.now(UTC))
                if command.command_type == "RequestActivity":
                    aid = UUID(command.payload["activity_id"])
                    journal.intent(
                        "activity",
                        aid,
                        {"scope": facts.scope_key, "run_id": str(run_id), "generation": 0},
                    )
                    journal.intent(
                        "timer",
                        uuid5(NAMESPACE_URL, f"opencitadel:activity-timeout:{aid}:0"),
                        {
                            "scope": facts.scope_key,
                            "run_id": str(run_id),
                            "activity_id": str(aid),
                            "timeout_at": command.payload["timeout_at"],
                        },
                    )
                result = await handler.handle(command)
                if result.status != "accepted":
                    raise ValueError("historical command was not accepted")
                await project(projector, scope)
                if command.command_type != "RequestActivity":
                    continue
                if not await facts.reconcile_activity(aid, handler):
                    worker = ActivityWorker(
                        store=FencedClaims(
                            PostgresActivityStore(
                                session_factory=facts.sessions,
                                authorization=KERNEL,
                                max_claim_attempts=settings.execution_activity_max_claim_attempts,
                            ),
                            facts,
                            aid,
                        ),
                        run_contexts=PostgresRunContextSource(
                            session_factory=facts.sessions, authorization=KERNEL
                        ),
                        run_service=RunService(orchestrator=handler),
                        registry=registry,
                        worker_id="capacity:" + binding["invocation"],
                        max_concurrency=1,
                        content_writer=content,
                        execution_gate=gate,
                        infrastructure_errors=(SQLAlchemyError, OSError, TimeoutError),
                        progress_sink=PostgresActivityProgressSink(
                            session_factory=facts.sessions, authorization=KERNEL
                        ),
                    )
                    with authorization_scope(authorized):
                        stats = await worker.run_once(now=datetime.now(UTC), limit=1)
                    if stats.claimed != 1 or stats.succeeded != 1:
                        raise ValueError("real retrieval failed or became uncertain")
                    if not await facts.reconcile_activity(aid, handler):
                        raise ValueError("actual settlement not recoverable")
                await project(projector, scope)
            receipt = await facts.parity(
                run_id,
                expected_events,
                expected_steps,
                views,
            )
            journal.acknowledge("run", run_id, receipt)
            total += expected_events
            await facts.drain_outbox(outbox)
        await facts.assert_exclusive()
        if total != (30_003 if _probe else 10_000_000):
            raise ValueError("historical total differs")
        await facts.converge(outbox)
        if not _probe:
            await facts.standard_totals(fixture_id)
        return {
            "converged": True,
            "status": "probe_ready" if _probe else "historical_ready",
            "runs": 1 if _probe else 100_000,
            "visible_steps": 10_000 if _probe else None,
            "run_id": str(run_id) if _probe else None,
            "scope_key": facts.scope_key,
            "cohort_id": str(fixture_id),
            "formal_events": total,
            "fixture_complete": False,
            "retention": "immutable_history_content_and_objects",
        }
    except Exception as failure:
        journal.intent(
            "failure",
            uuid4(),
            {"error_type": type(failure).__name__, "private_detail": str(failure)},
        )
        # Cancel only exact journaled incomplete Runs through ordinary commands.
        # These additional facts invalidate this standard attempt permanently.
        journal.intent(
            "invalid", manifest["fixture_id"], {"reason": "historical_construction_failed"}
        )
        cleanup = ObservedHandler(
            SqlAlchemyExecutionOrchestrator(
                session_factory=facts.sessions,
                aggregates={"run": RunAggregate()},
                authorization=KERNEL,
                activity_timeout=PostgresActivityTimeoutGuard(registry),
                evaluation_execution=gate,
            ),
            journal,
        )
        for identity, _record in journal.records("run"):
            if _record["body"]["scope"] != facts.scope_key:
                continue
            events = await facts.events(identity)
            if not events:
                continue
            state = RunAggregate().initial_state(identity)
            for event in events:
                state = RunAggregate().evolve(state, event)
            if state.status.value in {"completed", "failed", "cancelled"}:
                continue
            cancellation = CommandEnvelope(
                command_id=uuid5(UUID(identity), "capacity:abort"),
                command_type="CancelRun",
                command_schema_version=1,
                stream_type="run",
                stream_id=identity,
                owner_user_id=scope.user_id,
                team_id=None,
                correlation_id=UUID(identity),
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload={"reason": "capacity_construction_failed"},
            )
            if (await cleanup.handle(cancellation)).status != "accepted":
                raise ValueError("owned cancellation did not converge") from failure
        await project(projector, scope)
        await facts.converge(outbox)
        return {
            "converged": True,
            "status": "failed",
            "fixture_complete": False,
            "failure_type": type(failure).__name__,
            "restoration": "eligible_after_driver_exit",
            "retention": "invalid_immutable_attempt",
        }
    finally:
        await shared.object_storage.drain()
