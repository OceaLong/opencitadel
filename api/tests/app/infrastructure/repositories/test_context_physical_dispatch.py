# ruff: noqa: F401,F811 -- isolated database fixture dependencies
"""Real prepare/compact Workers, native HTTP adapter, budget and recovery receipts."""

import json
import resource
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter_ns, process_time_ns
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from openai import AsyncOpenAI
from sqlalchemy import text

from app.application.evaluation.budget_candidates import FrozenBudgetCandidates
from app.application.execution.activities.context_compact import ContextCompactActivityHandler
from app.application.execution.activities.context_prepare import ContextPrepareActivityHandler
from app.application.execution.activities.model_call import ModelCallActivityHandler
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.activity_worker import ActivityWorker
from app.application.execution.context_compaction import summary_activity_key
from app.application.execution.context_compiler import compile_context
from app.application.execution.decisions.base import activity_identity
from app.application.execution.definition_registry import DefinitionRegistry
from app.application.execution.run_context import run_execution_context
from app.application.execution.run_service import RunService
from app.application.ports.context_preparation import ContextCompilerInput
from app.application.services.execution_usage_service import ExecutionUsageService
from app.application.services.inference_model_service import InferenceModelService
from app.domain.evaluation.budget import BudgetPolicy
from app.domain.evaluation.context_profile import context_profiles
from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
from app.domain.execution.activity import ActivityContext
from app.domain.execution.context_models import ContextBoundary, ContextPolicy
from app.domain.execution.context_summary import ContextCompactBinding
from app.domain.execution.definition import HandlerImplementation
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
from app.infrastructure.execution.context_model_dispatch import (
    ContextModelDispatchValidator,
    PreparedContextValidation,
)
from app.infrastructure.execution.context_summary_dispatch import ContextSummaryDispatchValidator
from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_context_summary import DurableContextSummaryProposalStore
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.repositories.db_evaluation_budget_policy_repository import (
    DBEvaluationBudgetPolicyRepository,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.execution.test_context_compiler import source
from tests.app.application.execution.test_context_objects import Storage
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.execution_test_support import authenticated_session_factory
from tests.app.infrastructure.execution.test_definition_lifecycle import manifest
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_execution_slots import (
    bound_run,
    run_command,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.fixture
async def object_sessions(budget_binding_fixture):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        budget_binding_fixture[-1]().session_factory.kw["bind"].url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
    )
    try:
        yield authenticated_session_factory(
            engine, signing_secret=load_deployment_settings().database_authorization_signing_secret
        )
    finally:
        await engine.dispose()


@pytest.fixture
async def compact_runtime(budget_binding_fixture, object_sessions, monkeypatch, request):
    fixture = budget_binding_fixture
    service, scope, principal, _, _, _, uow = fixture
    binding, snapshot = await bound_run(fixture)
    options = getattr(request, "param", {})
    ordinary = options.get("ordinary", False)
    automatic = options.get("iterations")
    if options.get("session_messages"):
        from app.domain.execution.family import RunFamily
        from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

        snapshot = derive_run_policy_snapshot(fixture[5].execution, RunFamily.ASK)
    if ordinary:
        binding = SimpleNamespace(
            run_id=uuid4(),
            source_entity_type="owned-context-fixture",
            source_entity_id=str(uuid4()),
            namespace_id=None,
            candidate_proof=binding.candidate_proof,
        )
    auth = AuthorizationContext.system("execution-kernel")
    sessions = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    physical, execution = (
        BudgetPolicy(
            revision=1, global_concurrency=10, user_concurrency=10, provider_concurrency=10
        ),
        ExecutionSlotPolicy(revision=1),
    )
    async with uow(auth) as work:
        if not ordinary:
            await work.evaluation_execution.prepare(scope, binding.run_id, execution)
        await DBEvaluationBudgetPolicyRepository(work.db_session).bootstrap(physical)
        await work.commit()
    policy = ContextPolicy(
        main_output_tokens=8192,
        compactions_per_round=4,
        **options.get("context_policy", {}),
    )
    definition = manifest(
        family=snapshot.family,
        context_policy=policy,
        summary_purposes=("production", "evaluation_subject"),
        handlers=tuple(
            HandlerImplementation(activity_type=kind, handler_version="1")
            for kind in (
                "context.prepare",
                "context.compact",
                "model.call",
                *(("retrieval.search", "tool.call") if automatic else ()),
            )
        ),
    )
    definitions = DefinitionRegistry()
    from app.application.execution.decisions import DECISION_PLANNERS

    definitions.register(
        definition,
        planner=DECISION_PLANNERS[definition.family].planner if automatic else lambda *args: None,
        compiler=compile_context,
    )
    if options.get("code_definition"):
        from app.composition.execution_definitions import (
            build_code_definitions,
            initial_definition_heads,
        )

        definitions = build_code_definitions()
        definition = next(
            item for item in initial_definition_heads(definitions) if item.family == snapshot.family
        )
        policy = definition.context_policy
        definitions = DefinitionRegistry()
        definitions.register(
            definition,
            planner=DECISION_PLANNERS[definition.family].planner,
            compiler=compile_context,
        )
    storage = Storage()
    if options.get("capture_content"):

        async def full_capture_read(key):
            value = storage.objects[key]
            storage.reads.append((key, len(value)))
            return value

        storage.get_bytes = full_capture_read
    retained_storage = storage
    if options.get("code_definition"):
        from app.infrastructure.execution.execution_object_lifecycle import (
            ExecutionObjectLifecycle,
            RetainedExecutionStorage,
        )

        retained_storage = RetainedExecutionStorage(
            ExecutionObjectLifecycle(
                object_sessions,
                storage,
                authorization=auth,
                signing_secret=load_deployment_settings().database_authorization_signing_secret,
            )
        )
    objects = ActivityObjectStore(retained_storage)
    replay = None
    if options.get("replay_runtime"):
        from app.application.evaluation.recording_authority import RecordingAuthority
        from app.application.evaluation.replay_runtime import ReplayRuntime

        replay = ReplayRuntime(RecordingAuthority(uow), storage, None)
    input_ref, input_digest = await objects.put_input(
        binding.run_id, {"message": "retain exact facts"}, scope=scope
    )
    knowledge = None
    if options.get("knowledge_evidence"):
        from tests.app.infrastructure.repositories.test_context_memory_authority import (
            seed_knowledge_evidence,
        )

        knowledge = await seed_knowledge_evidence(uow, scope)
    candidates = FrozenBudgetCandidates(service.budgets.inventory, binding.candidate_proof)
    summary_candidates = candidates.for_context_summary(output_tokens=policy.summary_output_tokens)
    boundary = ContextBoundary(
        current_run_id=binding.run_id,
        scope_key=f"user:{scope.user_id}",
        collection_id="owned-context-fixture",
        through_position=1000,
        current_input_digest=input_digest,
    )
    sources = tuple(
        item.model_copy(
            update={"ref": item.ref.model_copy(update={"scope_key": boundary.scope_key})}
        )
        for item in [
            source(i, f"fact {i}: {i * 17}; " + "evidence " * (54 if options.get("large") else 16))
            for i in range(2 if options.get("ready") else (200 if options.get("large") else 180))
        ]
    )
    inputs = ContextCompilerInput(
        pin=definition.pin,
        boundary=boundary,
        policy=policy,
        candidates=context_profiles(candidates, output_tokens=8192),
        summary_candidates=context_profiles(summary_candidates, output_tokens=1024),
        sources=sources,
    )
    plan = compile_context(
        pin=inputs.pin,
        boundary=boundary,
        policy=policy,
        candidates=inputs.candidates,
        summary_candidates=inputs.summary_candidates,
        sources=sources,
    )
    assert plan.status == ("ready" if options.get("ready") else "needs_compaction")
    checks = SimpleNamespace(revoked=False, invalid=False, revoke_in_uow=False, count=0)

    class Sources:
        async def validate(self, cut, refs):
            assert cut == boundary
            assert {ref.source_id for ref in refs} <= {item.ref.source_id for item in sources}
            checks.count += 1
            if checks.revoked:
                raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")

        async def validate_in_uow(self, work, cut, refs):
            await work.db_session.execute(
                text("SELECT id FROM users WHERE id=:id FOR SHARE"), {"id": principal.user_id}
            )
            await work.execution_usage.authorize_principal(scope, principal)
            if checks.revoke_in_uow:
                raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")
            await self.validate(cut, refs)

    context_runtime = None
    if options.get("real_preparation"):
        from app.composition.context_runtime import build_context_runtime

        context_runtime = build_context_runtime(
            uow_factory=uow,
            objects=objects,
            storage=retained_storage,
            signing_secret="owned-long-context-cursor",
        )
        validator = context_runtime.sources
    else:
        validator = Sources()

    class Contracts:
        async def validate_in_uow(self, work, state, manifest):
            assert manifest.tools == ()
            assert manifest.tool_contract_digests == ()

    contracts = Contracts()
    if options.get("real_preparation"):
        from app.infrastructure.execution.context_contract_validation import (
            ContextContractValidator,
        )

        contracts = ContextContractValidator()
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
        summary_validator=(
            context_runtime.summary_validator
            if context_runtime
            else ContextSummaryDispatchValidator(objects, validator)
        ),
        model_validator=(
            context_runtime.model_validator
            if context_runtime
            else ContextModelDispatchValidator(objects, validator, contracts)
        ),
    )

    @asynccontextmanager
    async def forbidden_repository():
        raise AssertionError("summary must use the physical UoW")
        yield

    usage = ExecutionUsageService(
        repository_context=forbidden_repository, physical_dispatch=dispatch
    )
    monkeypatch.setattr(ApiKeyCipher, "decrypt_versioned", lambda self, value: "fixture-credential")
    models = InferenceModelService(uow, InfrastructureInferenceProviderAdapter(), None, None)
    sends, originals = [], []
    catalog, pack, responses = SimpleNamespace(), None, None
    if automatic:
        from tests.app.integration.long_context_support import ModelResponses, tool_catalog

        reader = None
        if (
            options.get("read_original")
            or options.get("read_early_tool")
            or options.get("read_collection")
        ):
            from app.application.execution.context_read_source import ContextReadSource
            from app.infrastructure.execution.context_original_reader import ContextOriginalReader
            from app.infrastructure.execution.context_read_service import ContextReadService

            reader = ContextReadService(
                uow_factory=uow,
                objects=objects,
                pages=ContextReadSource(
                    reader=ContextOriginalReader(
                        uow_factory=uow,
                        sources=validator,
                        objects=objects,
                        storage=retained_storage,
                    ),
                    validator=validator,
                    signing_secret="owned-long-context-cursor",
                ),
            )
        catalog, pack = tool_catalog(uow, source_reader=reader)
        catalog._replay = replay
        if options.get("capture_contracts"):
            from app.application.evaluation.contract_capture import ContractCapture

            catalog._contract_capture = ContractCapture(uow)
        if knowledge is not None:
            from app.domain.models.tool_result import ToolResult
            from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool

            native = KnowledgeBaseTool(
                uow,
                "ctx-kb",
                "ctx-v1",
                policy=snapshot.family_policy.knowledge_retrieval,
                owner_scope=scope,
            )

            async def retrieve_fixed(*args, **kwargs):
                # Retrieval text is controlled; the native catalog's provenance,
                # version and owner checks use the actual database.
                return ToolResult(
                    data="Do not renew ORD83; amount=5749",
                    citations=list(knowledge.evidence.knowledge[0].citations),
                )

            native.invoke = retrieve_fixed
            original_build = catalog._build

            async def build_with_knowledge(payload, context):
                built = await original_build(payload, context)
                built.retrieval.append(("knowledge_base", native, "kb_search"))
                return built

            catalog._build = build_with_knowledge
        original_source = f"input:{binding.run_id}" if reader else None
        if options.get("read_collection"):
            original_source = f"collection:{binding.run_id}:0000"
        if options.get("read_early_tool"):
            first_tool = activity_identity(
                SimpleNamespace(run_id=binding.run_id, retry_generation=0), "tool:0:0:read-0-0"
            )
            original_source = f"activity:{first_tool}:0"
        responses = ModelResponses(
            automatic,
            original_source=original_source,
            original_round=automatic - 1 if options.get("read_early_tool") else 0,
            ask=options.get("session_messages", False),
        )
        if options.get("response_factory"):
            responses = options["response_factory"](responses)

    async def http_send(request):
        wire = json.loads(request.content)
        if not automatic:
            assert wire["max_completion_tokens"] == (8192 if options.get("ready") else 1024)
            assert not wire.get("tools")
        async with uow(auth) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_dispatches")
                )
                == len(sends) + 1
            )
            demand = await work.db_session.scalar(
                text(
                    "SELECT demand FROM evaluation_budget_reservations ORDER BY created_at DESC LIMIT 1"
                )
            )
            if options.get("replay_runtime"):
                assert demand["purpose"] in {"production", "evaluation_subject"}
                if demand["purpose"] == "evaluation_subject":
                    assert demand["batch_id"]
            else:
                assert demand["purpose"] == ("production" if ordinary else "evaluation_subject")
            if not ordinary:
                assert demand["batch_id"] == str(binding.namespace_id)
        sends.append(wire)
        if options.get("summary_transport_timeout"):
            raise httpx.ReadTimeout("owned summary response timeout", request=request)
        if options.get("fallback_before_first_chunk"):
            if wire["model"] == "claude-haiku-4-5-20251001":
                if wire["max_tokens"] == 1024:
                    content = responses.message(
                        {"max_completion_tokens": 1024, "messages": wire["messages"]}
                    )["content"]
                else:
                    assert wire["max_tokens"] == 4096
                    responses.main.append(wire)
                    content = "ARCHIVE-Z19 amount=7341; do not renew."
                return httpx.Response(
                    200,
                    json={
                        "id": "fallback-response",
                        "type": "message",
                        "role": "assistant",
                        "model": wire["model"],
                        "content": [{"type": "text", "text": content}],
                        "stop_reason": "end_turn",
                        "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 10},
                    },
                )
            if wire["max_completion_tokens"] == (1024 if options.get("fallback_summary") else 4096):
                return httpx.Response(
                    429,
                    json={
                        "error": {
                            "message": "insufficient_quota",
                            "code": "insufficient_quota",
                            "type": "insufficient_quota",
                        }
                    },
                )
        facts = {
            "facts": [
                {
                    "kind": "fact",
                    "text": "exact retained fact",
                    "source_ids": [
                        sources[0].ref.source_id
                        if options.get("ready")
                        else plan.compaction.chunks[len(sends) - 1].sources[0].source_id
                    ],
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "id": "summary-fixture",
                "created": 0,
                "object": "chat.completion",
                "model": wire["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": (
                            await responses.respond(wire)
                            if hasattr(responses, "respond")
                            else responses.message(wire)
                        )
                        if automatic
                        else {
                            "role": "assistant",
                            "content": "final answer"
                            if options.get("ready")
                            else ("invalid" if checks.invalid else json.dumps(facts)),
                            **(
                                {
                                    "tool_calls": [
                                        {
                                            "id": "forbidden-final-tool",
                                            "type": "function",
                                            "function": {
                                                "name": "read_evidence",
                                                "arguments": options["forbidden_tool_arguments"],
                                            },
                                        }
                                    ]
                                }
                                if "forbidden_tool_arguments" in options
                                else {}
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    def native_client(model, **kwargs):
        if model.provider.value == "anthropic":
            from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM

            adapter = AnthropicLLM(model)
            originals.append(SimpleNamespace(close=adapter._client.aclose))
            adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(http_send))
            return adapter
        adapter = OpenAILLM(model)
        originals.append(adapter._client)
        adapter._client = AsyncOpenAI(
            base_url=model.base_url,
            api_key="fixture-credential",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(http_send)),
        )
        return adapter

    def client_factory(model, **kwargs):
        native = native_client(model, **kwargs)
        if not options.get("fallback_before_first_chunk"):
            return native
        from tests.app.infrastructure.external.llm.test_resilient_llm import ResilientLLMClient

        return ResilientLLMClient(
            native,
            model,
            **kwargs,
            model_client_factory=SimpleNamespace(create_model_client=native_client),
        )

    proposals = DurableContextSummaryProposalStore(uow)
    compact = ContextCompactActivityHandler(
        objects=objects,
        definitions=definitions,
        proposals=proposals,
        sources=validator,
        usage=usage,
        models=models,
        client_factory=client_factory,
        replay=replay,
    )

    async def materialize(*args, **kwargs):
        return inputs

    preparation = SimpleNamespace(materialize=materialize)
    preparation_reads = []
    if context_runtime is not None:
        prepare_handler, compact = context_runtime.handlers(
            definitions=definitions,
            tools=catalog,
            usage=usage,
            models=models,
            client_factory=client_factory,
            replay=replay,
        )
        preparation = prepare_handler.preparation
        real_materialize = preparation.materialize

        async def measured_materialize(request, context, payload, *, definition):
            start = len(storage.reads)
            started_at, started_cpu = perf_counter_ns(), process_time_ns()
            value = await real_materialize(request, context, payload, definition=definition)
            reads = storage.reads[start:]
            preparation_reads.append(
                {
                    "activity_id": str(request.activity_id),
                    "elapsed_ms": (perf_counter_ns() - started_at) / 1_000_000,
                    "cpu_ms": (process_time_ns() - started_cpu) / 1_000_000,
                    "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    "read_count": len(reads),
                    "read_bytes": sum(
                        min(limit, len(storage.objects[key])) for key, limit in reads
                    ),
                }
            )
            return value

        preparation = SimpleNamespace(materialize=measured_materialize)
    registry = ActivityRegistry()
    registry.register(
        ContextPrepareActivityHandler(
            objects=objects,
            definitions=definitions,
            preparation=preparation,
        )
    )
    registry.register(compact)
    registry.register(
        ModelCallActivityHandler(
            objects=objects,
            models=models,
            tools=catalog,
            execution_usage=usage,
            client_factory=client_factory,
            context_validation=PreparedContextValidation(validator),
        )
    )
    if automatic:
        from app.application.execution.activities.retrieval import RetrievalActivityHandler
        from app.application.execution.activities.tool_call import ToolCallActivityHandler
        from app.application.execution.activities.tool_intent_refresh import (
            ToolIntentRefreshActivityHandler,
        )

        registry.register(ToolIntentRefreshActivityHandler(objects=objects, tools=catalog))

        async def recall(*args, **kwargs):
            return None

        async def recall_context(*args, query, **kwargs):
            from app.domain.execution.context_models import context_digest
            from app.domain.execution.context_resources import (
                ContextMemoryRecall,
                ContextMemoryVersion,
            )
            from app.domain.models.memory_entry import MemoryEntry

            if options.get("memory_evidence") and (
                not options.get("memory_first_turn") or query.startswith("Fact 0:")
            ):
                entry = MemoryEntry(
                    id="physical-context-memory",
                    owner_user_id=scope.user_id,
                    content="ORD83 must not be renewed; exact amount=5749",
                )
                async with uow(auth) as work:
                    await work.memory_entry.save(entry)
                    await work.commit()
                return ContextMemoryRecall(
                    query_digest=context_digest(query),
                    content=entry.content,
                    entries=(ContextMemoryVersion.from_entry(entry),),
                )
            return ContextMemoryRecall(query_digest=context_digest(query), content="", entries=())

        registry.register(
            RetrievalActivityHandler(
                objects=objects,
                tools=catalog,
                memories=SimpleNamespace(recall_for_session=recall, recall_context=recall_context),
                execution_usage=usage,
                replay=replay,
            )
        )
        registry.register(
            ToolCallActivityHandler(objects=objects, tools=catalog, execution_usage=usage)
        )
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions,
        aggregates={"run": RunAggregate()},
        authorization=auth,
        evaluation_execution=EvaluationExecutionGuard(
            execution, session_factory=sessions, authorization=auth
        ),
    )

    async def command(name, body=None, version=1, lease=None):
        envelope = run_command(fixture, binding, snapshot, name, body)
        if name == "CreateRun":
            envelope = envelope.model_copy(
                update={
                    "payload": {
                        **envelope.payload,
                        "semantic_payload": {"input_ref": input_ref, "input_digest": input_digest},
                        "definition_pin": definition.pin.model_dump(mode="json"),
                        "definition_manifest": definition.model_dump(mode="json"),
                    }
                }
            )
        result = await orchestrator.handle(
            envelope.model_copy(
                update={"command_schema_version": version, "activity_lease_proof": lease}
            )
        )
        assert result.status == "accepted", result

    if ordinary:
        from app.application.execution.admission import RunAdmissionService
        from app.application.execution.command_ingress import CommandIngress
        from app.application.execution.inbox_worker import InboxWorker
        from app.infrastructure.execution.admission_definition_resolver import (
            AdmissionDefinitionResolver,
        )
        from app.infrastructure.execution.context_file_authority import ContextInputResourceResolver
        from app.infrastructure.execution.postgres_definition_store import PostgresDefinitionStore
        from app.infrastructure.execution.postgres_inbox_source import PostgresInboxSource
        from app.infrastructure.repositories.postgres_runtime_policy_repository import (
            PostgresRuntimePolicyRepository,
        )
        from tests.app.application.execution.test_policy_admission import _PolicyHeads

        policies = PostgresRuntimePolicyRepository(session_factory=sessions, authorization=auth)
        await policies.seed_if_missing(
            execution_policy=fixture[5].execution.revision.policy,
            operations_policy=fixture[5].operations.revision.policy,
            actor="context-owned-test",
            note="Real ordinary admission policy",
        )
        real_pair = await policies.load_active_pair()
        if automatic:
            from tests.app.integration.long_context_support import activate_iterations

            real_pair = await activate_iterations(service.policies, principal, scope, automatic)

        attachments = []
        async with uow(auth) as work:
            await PostgresDefinitionStore(work.db_session).seed_head(definition)
            if options.get("attachment_evidence"):
                import hashlib
                from io import BytesIO

                from PIL import Image

                from app.domain.models.file import File

                encoded = BytesIO()
                Image.new("RGB", (32, 32), color=(20, 80, 140)).save(encoded, format="PNG")
                if options.get("large_image"):
                    # The inline image limit is 256 KiB; a larger compressible image
                    # tests the compressed document branch without exceeding it.
                    encoded = BytesIO()
                    Image.new("RGB", (512, 512), color=(20, 80, 140)).save(
                        encoded, format="PNG", compress_level=1
                    )
                for name, mime, data in (
                    ("picture", "image/png", encoded.getvalue()),
                    (
                        "document",
                        "text/plain",
                        (
                            "Background paragraph. " * 50000
                            + "\nORD83: exact amount 7341; never renew.\n"
                        ).encode(),
                    ),
                ):
                    key = f"owned-context-files/{name}"
                    await storage.put_bytes(key, data)
                    await work.file.save(
                        File(
                            id="context-" + name,
                            filename=name,
                            key=key,
                            mime_type=mime,
                            size=len(data),
                            content_digest=hashlib.sha256(data).hexdigest(),
                            object_identity=str(uuid4()),
                            owner_user_id=scope.user_id,
                        )
                    )
                    attachments.append({"file_id": "context-" + name})
            if options.get("skill_evidence"):
                from app.domain.models.skill import Skill

                await work.skill.save(
                    Skill(
                        id="context-skill",
                        slug="context-skill",
                        owner_user_id=scope.user_id,
                        visibility="private",
                        system_prompt="Never renew ORD83; retain amount 7341.",
                        body="Selected instructions survive compaction.",
                        allowed_tools=[],
                    )
                )
            await work.commit()
        admission = RunAdmissionService(
            command_ingress=CommandIngress(writer=None),
            activity_objects=objects,
            policy_heads=_PolicyHeads(real_pair.execution),
            definition_resolver=AdmissionDefinitionResolver(),
            configuration_resolver=usage.admission_resolver(models),
            context_resource_resolver=ContextInputResourceResolver(objects),
        )
        async with uow() as work:
            admitted_id = await admission.admit(
                family=snapshot.family,
                source_entity_type=binding.source_entity_type,
                source_entity_id=binding.source_entity_id,
                owner_scope=scope,
                run_id=binding.run_id,
                private_input={
                    "message": options.get("message", "retain exact facts"),
                    "model_id": "e02-model",
                    **({"attachments": attachments} if attachments else {}),
                    **({"skill_id": "context-skill"} if options.get("skill_evidence") else {}),
                    **(knowledge.payload if knowledge is not None else {}),
                    **(
                        {"conversation": options["provided_history"]}
                        if options.get("provided_history")
                        else {}
                    ),
                },
                public_input={},
                command_sink=work.execution_commands,
                inference_read_context=work,
            )
            assert admitted_id == binding.run_id
            await work.commit()
        delivered = await InboxWorker(
            source=PostgresInboxSource(session_factory=sessions, authorization=auth),
            handler=orchestrator,
        ).run_once(now=datetime.now(UTC), limit=1)
        assert delivered.accepted == 1
        async with uow(auth) as work:
            admitted_state = (
                await PostgresVerifiedRunLoader(work.db_session).load(
                    str(binding.run_id), owner_scope=(scope.user_id, None)
                )
            ).replay.state
        input_ref = admitted_state.semantic_payload["input_ref"]
        input_digest = admitted_state.semantic_payload["input_digest"]
        boundary = boundary.model_copy(update={"current_input_digest": input_digest})
        inputs = inputs.model_copy(update={"boundary": boundary})
        plan = compile_context(
            pin=inputs.pin,
            boundary=boundary,
            policy=policy,
            candidates=inputs.candidates,
            summary_candidates=inputs.summary_candidates,
            sources=sources,
        )
    else:
        await command("CreateRun", version=3)
    if automatic:
        from redis.asyncio import Redis

        from app.composition.kernel_runtime import build_execution_kernel_runtime
        from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter

        content_writer = (
            ExecutionContentWriter(session_factory=sessions, authorization=auth, objects=objects)
            if options.get("capture_content")
            else None
        )
        # The outbox publisher is not used by this direct poll driver.
        redis = Redis()
        runtime = build_execution_kernel_runtime(
            session_factory=sessions,
            redis=redis,
            authorization=auth,
            activity_registry=registry,
            definitions=definitions,
            worker_id="long-context-worker",
            content_writer=content_writer,
            evaluation_execution=EvaluationExecutionGuard(
                execution, session_factory=sessions, authorization=auth
            ),
        )
        try:
            yield SimpleNamespace(
                kernel=runtime,
                sessions=sessions,
                uow=uow,
                auth=auth,
                scope=scope,
                binding=binding,
                objects=objects,
                storage=storage,
                responses=responses,
                pack=pack,
                sends=sends,
                policy=real_pair.execution,
                source_validator=validator,
                admission=admission,
                principal=principal,
                api_uow=lambda: service.uow_factory(
                    AuthorizationContext.for_principal(principal, scope=scope)
                ),
                session_messages=options.get("session_messages", 0),
                memory_first_turn=options.get("memory_first_turn", False),
                attachment_first_turn=options.get("attachment_first_turn", False),
                purge_between_turns=options.get("purge_between_turns", False),
                preparation_reads=preparation_reads,
                replay=replay,
                registry=registry,
                definition=definition,
                definitions=definitions,
                catalog=catalog,
                orchestrator=orchestrator,
                command=command,
            )
        finally:
            await redis.aclose()
            for original in originals:
                await original.close()
        return
    await command("StartRun")
    prepare_id = uuid4()
    await command(
        "RequestActivity",
        {
            "activity_id": str(prepare_id),
            "activity_type": "context.prepare",
            "input_ref": input_ref,
            "input_digest": input_digest,
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
        4,
    )
    projector = PostgresFormalProjector(session_factory=sessions, authorization=auth)
    await projector.run_once(scope, limit=100, notify=False)
    store = PostgresActivityStore(session_factory=sessions, authorization=auth)
    worker = ActivityWorker(
        store=store,
        run_contexts=PostgresRunContextSource(session_factory=sessions, authorization=auth),
        run_service=RunService(orchestrator=orchestrator),
        registry=registry,
        definitions=definitions,
        worker_id="compact-worker",
    )
    assert (await worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
    async with uow(auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                str(binding.run_id), owner_scope=(scope.user_id, None)
            )
        ).replay.state
    prepared_ref = next(
        ref for identity, _, ref, _, _ in state.activity_results if identity == prepare_id
    )
    if options.get("real_preparation"):
        from app.domain.execution.context_models import ContextCompilation

        plan = ContextCompilation.model_validate(
            (await objects.load_context_document(prepared_ref))["compilation"]
        )
    if options.get("ready"):
        activity_id = activity_identity(state, "model:0")
        activity_kind = "model.call"
        activity_payload = {
            "allow_tools": False,
            "round": 0,
            "context_ref": prepared_ref,
            "context_digest": plan.manifest.digest,
            "history_refs": ["unused-legacy-reference"] * 400,
        }
    else:
        chunk = plan.compaction.chunks[0]
        compact_binding = ContextCompactBinding(
            prepare_activity_id=prepare_id,
            prepare_ref=prepared_ref,
            compaction_digest=plan.compaction.digest,
            chunk_digest=chunk.digest,
            round_index=0,
            ordinal=chunk.ordinal,
        )
        activity_id = activity_identity(state, summary_activity_key(compact_binding))
        activity_kind = "context.compact"
        activity_payload = compact_binding.model_dump(mode="json")
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity_id),
            "activity_type": activity_kind,
            "input_ref": input_ref,
            "input_digest": input_digest,
            "input_payload": activity_payload,
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
        4,
    )
    await projector.run_once(scope, limit=100, notify=False)
    try:
        yield SimpleNamespace(
            uow=uow,
            auth=auth,
            worker=worker,
            compact=compact,
            store=store,
            command=command,
            checks=checks,
            sends=sends,
            activity_id=activity_id,
            binding=binding,
            scope=scope,
            proposals=proposals,
            objects=objects,
            definitions=definitions,
            plan=plan.compaction,
            model_manifest=plan.manifest,
            prepare_id=prepare_id,
            prepared_ref=prepared_ref,
            input_ref=input_ref,
            input_digest=input_digest,
            sources=sources,
            source_validator=validator,
            usage=usage,
        )
    finally:
        for original in originals:
            await original.close()


async def test_a06_persisted_summary_before_completion_recovers_without_second_native_send(
    compact_runtime,
):
    runtime = compact_runtime
    claim = (
        await runtime.store.claim(
            now=datetime.now(UTC), limit=1, worker_id="before-crash", claim_ttl=timedelta(minutes=1)
        )
    )[0]
    assert await runtime.store.mark_call_started(claim, now=datetime.now(UTC))
    await runtime.command(
        "MarkActivityCallStarted",
        {
            "activity_id": str(claim.request.activity_id),
            "generation": 0,
            "claim_generation": claim.claim_generation,
        },
        2,
        claim.lease_proof,
    )
    async with runtime.uow(runtime.auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                str(runtime.binding.run_id), owner_scope=(runtime.scope.user_id, None)
            )
        ).replay.state
    context = ActivityContext(
        worker_id=claim.worker_id,
        claim_generation=claim.claim_generation,
        idempotency_key=str(claim.request.activity_id),
        owner_user_id=runtime.scope.user_id,
        team_id=None,
        run=run_execution_context(state),
    )
    first = await runtime.compact.execute(claim.request, context)
    assert first.status == "succeeded"
    assert len(runtime.sends) == 1
    assert await runtime.compact.recovery_safe(claim.request, context.run)
    # Drop the returned outcome, exactly as a process loss before CompleteActivity.
    async with runtime.uow(runtime.auth) as work:
        await work.db_session.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=clock_timestamp()-INTERVAL '1 second' WHERE activity_id=:id"
            ),
            {"id": claim.request.activity_id},
        )
        await work.commit()
    stats = await runtime.worker.run_once(now=datetime.now(UTC), limit=1)
    assert stats.succeeded == 1
    assert len(runtime.sends) == 1
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_context_summary_proposals")
            )
            == 1
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 1
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_settlements"))
            == 1
        )
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM execution_events WHERE event_type='ActivityCompleted' AND public_payload->>'activity_id'=:activity"
                ),
                {"activity": str(runtime.activity_id)},
            )
            == 1
        )


async def test_c08_revoked_summary_sources_produce_zero_native_sends_and_receipts(compact_runtime):
    runtime = compact_runtime
    runtime.checks.revoked = True
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).failed == 1
    assert runtime.sends == []
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_context_summary_proposals")
            )
            == 0
        )


async def test_c05_invalid_summary_has_one_billed_send_and_no_recoverable_proposal(compact_runtime):
    runtime = compact_runtime
    runtime.checks.invalid = True
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).failed == 1
    assert len(runtime.sends) == 1
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_settlements"))
            == 1
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_context_summary_proposals")
            )
            == 0
        )


@pytest.mark.parametrize("compact_runtime", [{"ordinary": True}], indirect=True)
async def test_production_admission_freezes_candidates_and_compact_uses_original_requester(
    compact_runtime,
):
    runtime = compact_runtime
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
    assert len(runtime.sends) == 1
    async with runtime.uow(runtime.auth) as work:
        row = (
            await work.db_session.execute(
                text(
                    "SELECT body FROM execution_configurations WHERE run_id=:run AND body->>'stage'='admission'"
                ),
                {"run": runtime.binding.run_id},
            )
        ).scalar_one()
        assert row["context_candidate_proof"]["candidates"][0]["output"] == 8192
        demand = await work.db_session.scalar(
            text("SELECT demand FROM evaluation_budget_reservations")
        )
        assert demand["purpose"] == "production"
        assert demand["requester"] == runtime.scope.user_id
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_context_summary_proposals")
            )
            == 1
        )
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )


async def test_c08_authority_change_inside_permit_transaction_rolls_back_all_receipts(
    compact_runtime,
):
    runtime = compact_runtime
    runtime.checks.revoke_in_uow = True
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).failed == 1
    assert runtime.sends == []
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations")
            )
            == 0
        )


@pytest.mark.parametrize("compact_runtime", [{"large": True}], indirect=True)
async def test_c09_more_than_three_windows_use_independent_bounded_chunk_receipts(compact_runtime):
    runtime = compact_runtime
    assert (
        sum(
            len(message.content.encode())
            for source in runtime.sources
            for message in source.messages
        )
        > 3 * runtime.plan.input_budget
    )
    assert len(runtime.plan.chunks) == 4
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
    for chunk in runtime.plan.chunks[1:]:
        async with runtime.uow(runtime.auth) as work:
            state = (
                await PostgresVerifiedRunLoader(work.db_session).load(
                    str(runtime.binding.run_id), owner_scope=(runtime.scope.user_id, None)
                )
            ).replay.state
        binding = ContextCompactBinding(
            prepare_activity_id=runtime.prepare_id,
            prepare_ref=runtime.prepared_ref,
            compaction_digest=runtime.plan.digest,
            chunk_digest=chunk.digest,
            round_index=0,
            ordinal=chunk.ordinal,
        )
        await runtime.command(
            "RequestActivity",
            {
                "activity_id": str(activity_identity(state, summary_activity_key(binding))),
                "activity_type": "context.compact",
                "input_ref": runtime.input_ref,
                "input_digest": runtime.input_digest,
                "input_payload": binding.model_dump(mode="json"),
                "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            },
            4,
        )
        assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
    assert len(runtime.sends) == 4
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_context_summary_proposals")
            )
            == 4
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_settlements"))
            == 4
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_model_logical_calls WHERE sends=1")
            )
            == 4
        )
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )


@pytest.mark.parametrize(
    "compact_runtime", [{"ready": True}, {"ready": True, "ordinary": True}], indirect=True
)
async def test_prepared_model_uses_frozen_messages_and_bypasses_four_hundred_legacy_refs(
    compact_runtime,
):
    runtime = compact_runtime
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
    assert len(runtime.sends) == 1
    assert runtime.sends[0]["messages"] == [
        message.wire() for message in runtime.model_manifest.messages
    ]
    async with runtime.uow(runtime.auth) as work:
        configuration = await work.db_session.scalar(
            text(
                "SELECT c.body FROM execution_model_dispatches d JOIN execution_configurations c ON c.scope_key=d.scope_key AND c.id=d.configuration_id"
            )
        )
        assert configuration["context_manifest"]["digest"] == runtime.model_manifest.digest
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_settlements"))
            == 1
        )


@pytest.mark.parametrize("compact_runtime", [{"ready": True}], indirect=True)
async def test_c08_model_authority_change_at_permit_commit_has_zero_native_sends(compact_runtime):
    runtime = compact_runtime
    runtime.checks.revoke_in_uow = True
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).failed == 1
    assert runtime.sends == []
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations")
            )
            == 0
        )


@pytest.mark.parametrize(
    "compact_runtime",
    [
        {"ready": True, "ordinary": True, "forbidden_tool_arguments": "{}"},
        {"ready": True, "ordinary": True, "forbidden_tool_arguments": "{"},
    ],
    indirect=True,
    ids=["well-formed", "malformed"],
)
async def test_tools_disabled_prepared_answer_rejects_all_provider_tool_intent(compact_runtime):
    runtime = compact_runtime
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).failed == 1
    assert len(runtime.sends) == 1
    assert not runtime.sends[0].get("tools")
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT failure_code FROM execution_activity_tasks WHERE activity_type='model.call'"
                )
            )
            == "MODEL_TOOL_CALL_FORBIDDEN"
        )
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM execution_activity_tasks WHERE activity_type='tool.call'"
                )
            )
            == 0
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_settlements"))
            == 1
        )


@pytest.mark.parametrize("compact_runtime", [{"ordinary": True}, {}], indirect=True)
async def test_context_authority_uses_consuming_run_requester_and_locks_live_identity(
    compact_runtime,
):
    from app.infrastructure.execution.context_authority import ContextRunAuthority

    runtime = compact_runtime
    boundary = runtime.plan.boundary.model_copy(update={"current_run_id": runtime.binding.run_id})
    async with runtime.uow(runtime.auth) as work:
        authority = ContextRunAuthority(
            work.db_session, signing_secret=work.evaluation_budget.secret
        )
        scope, epoch = await authority.lock(boundary)
        assert scope == runtime.scope
        assert len(epoch) == 64
        with pytest.raises(ValueError, match="REQUESTER_UNAVAILABLE"):
            await authority.lock(boundary.model_copy(update={"current_run_id": uuid4()}))
        with pytest.raises(ValueError, match="REQUESTER_UNAVAILABLE"):
            await authority.lock(boundary.model_copy(update={"scope_key": "team:other"}))
        await work.db_session.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:user"),
            {"user": scope.user_id},
        )
        with pytest.raises(ValueError, match="AUTHORIZATION_REVOKED"):
            await authority.lock(boundary)
        # Caller rollback restores the isolated principal after this test.


@pytest.mark.parametrize(
    "compact_runtime", [{"ordinary": True, "ready": True, "real_preparation": True}], indirect=True
)
async def test_real_preparation_and_live_source_authority_reach_native_permit(compact_runtime):
    runtime = compact_runtime
    manifest = runtime.model_manifest
    assert manifest.boundary.current_run_id == runtime.binding.run_id
    assert manifest.boundary.collection_id == str(runtime.binding.run_id)
    assert [message.role for message in manifest.messages] == ["system", "user"]
    assert manifest.messages[-1].content == "retain exact facts"
    assert {ref.source_id for ref in manifest.original_closure} == {
        f"input:{runtime.binding.run_id}",
        f"platform:{runtime.binding.run_id}",
    }
    result = await runtime.worker.run_once(now=datetime.now(UTC), limit=1)
    assert result.succeeded == 1
    assert len(runtime.sends) == 1
    assert runtime.sends[0]["messages"] == [message.wire() for message in manifest.messages]
    async with runtime.uow(runtime.auth) as work:
        await work.db_session.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:user"),
            {"user": runtime.scope.user_id},
        )
        with pytest.raises(ValueError, match="AUTHORIZATION_REVOKED"):
            await runtime.source_validator.validate_in_uow(
                work, manifest.boundary, manifest.original_closure
            )


@pytest.mark.parametrize(
    "compact_runtime", [{"ordinary": True, "ready": True, "real_preparation": True}], indirect=True
)
async def test_live_revocation_between_validation_and_permit_is_known_unsent(
    compact_runtime,
    monkeypatch,
):
    runtime = compact_runtime
    validate = runtime.source_validator.validate
    revoked = False

    async def revoke_after_validation(boundary, refs):
        nonlocal revoked
        await validate(boundary, refs)
        if not revoked:
            revoked = True
            async with runtime.uow(runtime.auth) as work:
                await work.db_session.execute(
                    text("UPDATE users SET token_version=token_version+1 WHERE id=:user"),
                    {"user": runtime.scope.user_id},
                )
                await work.commit()

    monkeypatch.setattr(runtime.source_validator, "validate", revoke_after_validation)
    result = await runtime.worker.run_once(now=datetime.now(UTC), limit=1)
    assert revoked
    assert result.failed == 1
    assert runtime.sends == []
    async with runtime.uow(runtime.auth) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations")
            )
            == 0
        )
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                runtime.binding.run_id,
                owner_scope=(runtime.scope.user_id, None),
            )
        ).replay.state
        assert (runtime.activity_id, "failed", 0) in state.settled_activities


@pytest.mark.parametrize(
    "compact_runtime", [{"ordinary": True, "ready": True, "real_preparation": True}], indirect=True
)
async def test_original_reader_uses_current_run_scope_and_rejects_forged_tool_lineage(
    compact_runtime,
):
    from app.application.execution.context_read_source import ContextReadSource
    from app.infrastructure.execution.context_original_reader import ContextOriginalReader
    from app.infrastructure.execution.context_read_service import ContextReadService

    runtime = compact_runtime
    reader = ContextOriginalReader(
        uow_factory=runtime.uow,
        sources=runtime.source_validator,
        objects=runtime.objects,
        storage=runtime.objects._storage,
    )
    pages = ContextReadSource(
        reader=reader, validator=runtime.source_validator, signing_secret="owned-original-cursor"
    )
    manifest = runtime.model_manifest
    first = await pages.read(manifest, source_id=f"input:{runtime.binding.run_id}", limit=4)
    second = await pages.read(manifest, source_id=first.source_id, cursor=first.next_cursor)
    assert first.text + second.text == "retain exact facts"
    with pytest.raises(ValueError, match="UNAVAILABLE"):
        await pages.read(manifest, source_id=f"input:{uuid4()}")
    async with runtime.uow(runtime.auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                runtime.binding.run_id, owner_scope=(runtime.scope.user_id, None)
            )
        ).replay.state
    context = ActivityContext(
        activity_id=runtime.activity_id,
        generation=0,
        worker_id="read-check",
        claim_generation=1,
        idempotency_key="read-check",
        owner_user_id=runtime.scope.user_id,
        team_id=None,
        run=run_execution_context(state),
    )
    count = len(runtime.objects._storage.reads)
    with pytest.raises(ValueError, match="READ_LINEAGE_INVALID"):
        await ContextReadService(
            uow_factory=runtime.uow, objects=runtime.objects, pages=pages
        ).read_for_activity(context, source_id=first.source_id)
    assert len(runtime.objects._storage.reads) == count


async def test_c05_summary_budget_denial_has_zero_native_sends(compact_runtime):
    from app.domain.evaluation.budget import BudgetDemand, BudgetSettlement
    from tests.app.infrastructure.repositories.test_evaluation_budget_repository import repository

    r = compact_runtime
    # Admission requires capacity for one call. Exhaust the remaining namespace
    # through the actual reserve/settle ledger after admission, before summary.
    demand = BudgetDemand.model_validate(
        {
            "scope": "user:" + r.scope.user_id,
            "requester": r.scope.user_id,
            "principal": {
                key: r.binding.requester[key] for key in ("user_id", "token_version", "global_role")
            },
            "purpose": "evaluation_subject",
            "batch_id": str(r.binding.namespace_id),
            "tokens": 3000000,
            "policy_revision": 1,
            "buckets": [
                {"key": "0:global", "slots": 10},
                {"key": "5:batch:" + str(r.binding.namespace_id), "tokens": 3000000},
            ],
        }
    )
    identity = str(uuid4())
    async with r.uow(r.auth) as work:
        await repository(work).reserve(identity, demand)
        await repository(work).settle(
            identity, demand, BudgetSettlement(tokens=3000000, evidence="prior-owned-usage")
        )
        await work.commit()
    assert (await r.worker.run_once(now=datetime.now(UTC), limit=1)).failed == 1
    assert r.sends == []
    async with r.uow(r.auth) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_context_summary_proposals")
            )
            == 0
        )
        codes = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT public_payload->>'failure_code' FROM execution_events WHERE event_type='ActivityFailed'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "CONTEXT_SUMMARY_BUDGET_EXCEEDED" in codes


@pytest.mark.parametrize("compact_runtime", [{"summary_transport_timeout": True}], indirect=True)
async def test_c05_summary_transport_timeout_preserves_unknown_without_resend(compact_runtime):
    r = compact_runtime
    stats = await r.worker.run_once(now=datetime.now(UTC), limit=1)
    assert stats.unknown == 1
    assert len(r.sends) == 1
    assert (await r.worker.run_once(now=datetime.now(UTC), limit=1)).claimed == 0
    assert len(r.sends) == 1
    async with r.uow(r.auth) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 1
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_context_summary_proposals")
            )
            == 0
        )
