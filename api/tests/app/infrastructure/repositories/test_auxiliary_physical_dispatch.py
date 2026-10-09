# ruff: noqa: F401,F811
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tests.app.infrastructure.repositories.test_evaluation_dispatch_transport import (
    ordinary_activity,
)
from tests.app.infrastructure.repositories.test_evaluation_physical_dispatch import (
    budget_binding_fixture,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    ready_dispatch,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_actual_tool_activity_embedding_send_uses_original_nonchat_admission(
    ready_dispatch, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.application.execution.activities.tool_call import ToolCallActivityHandler
    from app.application.services.embedding_service import EmbeddingService
    from app.infrastructure.external.inference.embedding import OpenAICompatibleEmbedding
    from tests.app.application.services.test_embedding_service import _resolved_embedding

    durable, usage, _, request, context, _ = await ordinary_activity(
        ready_dispatch,
        activity_type="tool.call",
        activity_payload={"tool_call": {"call_id": "tool-1", "name": "retrieve", "arguments": {}}},
        admission_payload={"build_id": "no-chat-message"},
    )
    fixture = ready_dispatch[0]
    _, scope, principal, _, _, _, uow = fixture

    async def create(**payload):
        async with uow(durable.authorization) as work:
            demand = await work.db_session.scalar(
                text("SELECT demand FROM evaluation_budget_reservations")
            )
            assert demand["requester"] == principal.user_id
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_dispatches")
                )
                == 1
            )
        return SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.1] * 1536)],
            model_dump=lambda: {"usage": {"prompt_tokens": 2, "total_tokens": 2}},
        )

    close = AsyncMock()
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(embeddings=SimpleNamespace(create=create), close=close),
    )
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.create_ssrf_safe_async_client",
        lambda **kwargs: None,
    )
    embeddings = EmbeddingService(
        SimpleNamespace(resolve=AsyncMock(return_value=_resolved_embedding())),
        SimpleNamespace(create_embedding=OpenAICompatibleEmbedding),
    )

    async def invoke(*args, **kwargs):
        await embeddings.embed(["query"], scope=scope)
        return {"success": True, "data": "done"}

    objects = SimpleNamespace(
        load_input=AsyncMock(return_value={}), put_result=AsyncMock(return_value="result")
    )
    handler = ToolCallActivityHandler(
        objects=objects, tools=SimpleNamespace(invoke=invoke), execution_usage=usage
    )
    assert (await handler.execute(request, context)).status == "succeeded"
    close.assert_awaited_once()
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )


async def test_direct_alias_and_evaluation_send_contend_for_same_physical_pool(
    ready_dispatch, datasets
):
    from app.application.ports.inference_dispatch import dispatch_context
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService
    from app.infrastructure.external.llm.dispatch import physical_send

    fixture, _, request, context, model, payload, physical, execution, _ = ready_dispatch
    service, scope, principal, _, _, _, uow = fixture
    physical = physical.model_copy(update={"revision": 2, "provider_concurrency": 1})
    async with uow(AuthorizationContext.system("execution-kernel")) as work:
        await work.evaluation_physical_policy.activate(physical, expected_revision=1)
        await work.commit()
    inventory = service.budgets.inventory
    alias_inventory = inventory.model_copy(
        update={
            "endpoints": {
                **inventory.endpoints,
                "direct-alias": inventory.endpoints[model.endpoint.id],
            }
        }
    )
    alias = model.model_copy(
        update={
            "endpoint": model.endpoint.model_copy(update={"id": "direct-alias"}),
            "model": model.model.model_copy(update={"endpoint_id": "direct-alias"}),
        }
    )
    direct = DirectPhysicalDispatchService(
        uow_factory=datasets[0].uow_factory, inventory=alias_inventory, physical_policy=physical
    )
    guard = direct.guard(
        scope, AuthorizationContext.for_principal(principal, scope=scope), purpose="probe"
    )

    async def timeout():
        raise TimeoutError("unknown provider result")

    with dispatch_context(guard, alias), pytest.raises(TimeoutError):
        await physical_send(timeout, {"model": alias.model_name}, provider="openai")
    evaluation = DurableBudgetDispatchService(
        uow_factory=uow, inventory=inventory, physical_policy=physical, execution_policy=execution
    )
    with pytest.raises(ValueError, match="concurrency_exhausted"):
        await evaluation.before_send(
            scope,
            request,
            context,
            model,
            payload,
            resolved={"policy_revision": "test", "tool_fingerprint": None},
        )
    identity = next(iter(guard.receipts))
    await guard.after_completion(identity, None)
    permit = await evaluation.before_send(
        scope,
        request,
        context,
        model,
        payload,
        resolved={"policy_revision": "test", "tool_fingerprint": None},
    )
    permit.consume()
    with pytest.raises(ValueError, match="concurrency_exhausted"):
        await guard.before_send(alias, {"model": alias.model_name})


async def test_actual_resource_build_chat_send_has_original_authority_and_closes(
    ready_dispatch, monkeypatch
):
    from unittest.mock import AsyncMock

    from openai.types.chat import ChatCompletion

    from app.application.execution.activities.resource_build import KnowledgeBuildActivityHandler
    from app.application.ports.inference_dispatch import dispatch_candidate
    from app.domain.models.build_progress import build_done
    from app.domain.runtime_policy import ExecutionPolicy, KnowledgeBaseExecutionPolicy
    from app.infrastructure.external.llm.openai_llm import OpenAILLM
    from tests.app.execution_test_support import run_execution_context_for

    policy = ExecutionPolicy(
        knowledge_base=KnowledgeBaseExecutionPolicy(
            graphrag={"enabled": True}, vector_enabled=False
        )
    )
    snapshot = run_execution_context_for("kb_ingest", policy=policy).policy_snapshot
    durable, usage, _, request, context, _ = await ordinary_activity(
        ready_dispatch,
        activity_type="knowledge.build",
        source_entity_type="resource_build",
        admission_payload={"build_id": "build"},
        policy_snapshot=snapshot,
    )
    _, _scope, principal, _, _, _, uow = ready_dispatch[0]
    model = ready_dispatch[4]

    async def send(**kwargs):
        async with uow(durable.authorization) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT demand->>'requester' FROM evaluation_budget_reservations")
                )
                == principal.user_id
            )
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_dispatches")
                )
                == 1
            )
        return ChatCompletion(
            id="fake",
            created=0,
            object="chat.completion",
            model=model.model_name,
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "{}"},
                }
            ],
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    close = AsyncMock()
    monkeypatch.setattr(
        "app.infrastructure.external.llm.openai_llm.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=send)), close=close
        ),
    )
    monkeypatch.setattr(
        "app.infrastructure.external.llm.openai_llm.create_ssrf_safe_async_client",
        lambda **kwargs: None,
    )

    class Client:
        def __init__(self):
            self.inner = OpenAILLM(model)

        async def invoke(self, *args):
            with dispatch_candidate(model):
                return await self.inner.invoke(*args)

        async def aclose(self):
            await self.inner.aclose()

    async def build(*args, graph_llm=None, **kwargs):
        assert graph_llm is not None
        await graph_llm.invoke([{"role": "user", "content": "graph"}])
        yield build_done()

    objects = SimpleNamespace(
        load_input=AsyncMock(return_value={"build_id": "build"}),
        put_result=AsyncMock(return_value="result"),
    )
    handler = KnowledgeBuildActivityHandler(
        objects=objects,
        pipeline=SimpleNamespace(run_build=build),
        models=SimpleNamespace(resolve_chat=AsyncMock(return_value=model)),
        client_factory=lambda *args, **kwargs: Client(),
        execution_usage=usage,
    )
    assert (await handler.execute(request, context)).status == "succeeded"
    close.assert_awaited_once()


async def test_evaluation_auxiliary_cannot_fall_through_to_direct_ordinary(ready_dispatch):
    from unittest.mock import AsyncMock

    from app.application.execution.activities.tool_call import ToolCallActivityHandler
    from app.application.ports.inference_dispatch import dispatch_candidate
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.dispatch import physical_send

    fixture, _, request, context, model, _, physical, execution, _ = ready_dispatch
    service, _, _, _, _, _, uow = fixture
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    request = request.model_copy(
        update={
            "input_payload": {"tool_call": {"call_id": "tool", "name": "vision", "arguments": {}}}
        }
    )
    send = AsyncMock()

    async def invoke(*args, **kwargs):
        with dispatch_candidate(model):
            await physical_send(send, {"model": model.model_name}, provider="openai")

    handler = ToolCallActivityHandler(
        objects=SimpleNamespace(load_input=AsyncMock(return_value={})),
        tools=SimpleNamespace(invoke=invoke),
        execution_usage=usage,
    )
    with pytest.raises(ValueError, match="auxiliary_evaluation_profile_unavailable"):
        await handler.execute(request, context)
    send.assert_not_awaited()
