"""Durable conversational Activities exchange only referenced JSON objects."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.execution.activities.model_call import ModelCallActivityHandler
from app.application.execution.activities.retrieval import RetrievalActivityHandler
from app.application.execution.activities.tool_call import ToolCallActivityHandler
from app.application.execution.tool_catalog import CatalogSnapshot, ToolDefinition
from app.domain.execution.activity import ActivityContext, ActivityRequest
from app.domain.models.inference import (
    ChatModelSettings,
    InferenceEndpoint,
    InferenceModel,
    ResolvedInferenceModel,
)
from app.domain.runtime_policy import ExecutionPolicy, ModelResiliencePolicy
from tests.app.execution_test_support import run_execution_context_for


class Objects:
    def __init__(self) -> None:
        self.input = {
            "message": "Find the design and write a report",
            "model_id": "model-1",
            "session_id": "session-1",
        }
        self.results = {
            "result://old-model": {
                "kind": "model",
                "message": {
                    "role": "assistant",
                    "content": "I will search",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "search_web",
                                "arguments": '{"query":"event sourcing"}',
                            },
                        }
                    ],
                },
            },
            "result://old-tool": {
                "kind": "tool",
                "message": {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "search_web",
                    "content": "result",
                },
            },
        }
        self.written = []

    async def load_input(self, *, key, expected_digest):
        assert key == "input://base"
        assert expected_digest == "a" * 64
        return self.input

    async def load_result(self, key):
        return self.results[key]

    async def put_result(self, activity_id, payload):
        self.written.append((activity_id, payload))
        return f"result://{activity_id}"


class Models:
    async def resolve_chat(self, model_id=None, *, scope):
        assert model_id == "model-1"
        assert scope.user_id == "user-1"
        return ResolvedInferenceModel(
            model=InferenceModel(
                id="model-1",
                endpoint_id="endpoint-1",
                display_name="test",
                model_name="test-model",
                settings=ChatModelSettings(),
            ),
            endpoint=InferenceEndpoint(
                id="endpoint-1",
                display_name="test",
                credential="secret",
            ),
        )


class Client:
    def __init__(self, usage=None) -> None:
        self.calls = []
        self.usage = usage

    async def invoke(self, messages, tools=None):
        self.calls.append((messages, tools))
        response = {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"filepath": "/work/report.md", "content": "done"}),
                    },
                }
            ],
        }
        if self.usage is not None:
            response["_usage"] = self.usage
        return response


class TokenUsage:
    def __init__(self) -> None:
        self.records = []

    async def record(self, **record):
        self.records.append(record)


class Catalog:
    def __init__(self) -> None:
        self.invocations = []

    async def definitions(self, payload, context):
        assert payload["session_id"] == "session-1"
        return CatalogSnapshot(
            definitions=(
                ToolDefinition(
                    name="write_file",
                    tool_schema={
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "parameters": {"type": "object"},
                        },
                    },
                    requires_approval=True,
                    risk_summary="Write workspace file",
                ),
            ),
            fingerprint="catalog-fp-1",
        )

    async def invoke(
        self,
        payload,
        context,
        *,
        name,
        arguments,
        expected_fingerprint=None,
        approval_feedback=None,
    ):
        self.invocations.append((payload, context, name, arguments))
        return {
            "success": True,
            "message": "written",
            "data": {"path": arguments["filepath"]},
        }

    async def retrieve(self, payload, context, *, query):
        assert payload["session_id"] == "session-1"
        return {
            "query": query,
            "sources": [{"kind": "knowledge_base", "content": "design"}],
        }


class Memories:
    def __init__(self) -> None:
        self.policy = None

    async def recall_for_session(self, session_id, *, owner_scope, policy):
        assert session_id == "session-1"
        assert owner_scope.user_id == "user-1"
        self.policy = policy
        return "<long_term_memory>fact</long_term_memory>"


def request(activity_type: str, *, input_payload=None) -> ActivityRequest:
    return ActivityRequest(
        activity_id=UUID("70000000-0000-0000-0000-000000000001"),
        activity_type=activity_type,
        aggregate_type="run",
        aggregate_id="80000000-0000-0000-0000-000000000001",
        generation=0,
        timeout_at=datetime(2026, 8, 25, tzinfo=UTC),
        input_ref="input://base",
        input_digest="a" * 64,
        input_payload=input_payload or {},
    )


CONTEXT = ActivityContext(
    worker_id="worker-1",
    claim_generation=1,
    idempotency_key="activity-1",
    owner_user_id="user-1",
    team_id=None,
    run=run_execution_context_for("agent"),
)


@pytest.mark.asyncio
async def test_model_activity_rehydrates_history_and_governs_tool_intent() -> None:
    objects = Objects()
    client = Client()
    handler = ModelCallActivityHandler(
        objects=objects,
        models=Models(),
        tools=Catalog(),
        client_factory=lambda *args, **kwargs: client,
    )

    outcome = await handler.execute(
        request(
            "model.call",
            input_payload={
                "allow_tools": True,
                "history_refs": ["result://old-model", "result://old-tool"],
                "round": 1,
            },
        ),
        CONTEXT,
    )

    assert outcome.status == "succeeded"
    assert outcome.decision_data == {
        "tool_calls": [
            {
                "call_id": "call-2",
                "name": "write_file",
                "arguments": {
                    "filepath": "/work/report.md",
                    "content": "done",
                },
                "requires_approval": True,
                "risk_summary": "Write workspace file",
                "approval_kind": "tool_effect",
            }
        ],
        # 目录快照摘要随决策落库（D9）
        "catalog": {"tool_names": ["write_file"], "fingerprint": "catalog-fp-1"},
    }
    messages, schemas = client.calls[0]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert schemas[0]["function"]["name"] == "write_file"
    assert objects.written[0][1]["kind"] == "model"


@pytest.mark.asyncio
async def test_model_activity_applies_temperature_and_records_provider_usage() -> None:
    objects = Objects()
    objects.input["temperature_override"] = 0.2
    client = Client(
        usage={
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "cached_tokens": 40,
            "cache_write_tokens": 5,
        }
    )
    usage = TokenUsage()
    captured = {}

    def client_factory(model, **kwargs):
        captured["model"] = model
        captured["policy"] = kwargs["policy"]
        return client

    handler = ModelCallActivityHandler(
        objects=objects,
        models=Models(),
        tools=Catalog(),
        token_usage=usage,
        client_factory=client_factory,
    )

    outcome = await handler.execute(
        request("model.call", input_payload={"round": 2, "allow_tools": True}),
        ActivityContext(
            worker_id="worker-1",
            claim_generation=1,
            idempotency_key="activity-1",
            owner_user_id="user-1",
            team_id=None,
            run=run_execution_context_for(
                "agent",
                policy=ExecutionPolicy(
                    model_resilience=ModelResiliencePolicy(max_attempts_per_call=7)
                ),
            ),
        ),
    )

    assert outcome.status == "succeeded"
    assert captured["model"].temperature == 0.2
    assert captured["policy"].max_attempts_per_call == 7
    assert usage.records == [
        {
            "session_id": "session-1",
            "agent": "agent",
            "step": "model:2",
            "model_id": "model-1",
            "model_name": "test-model",
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "cached_tokens": 40,
            "cache_write_tokens": 5,
            "cache_metric_source": "provider",
            "owner_user_id": "user-1",
            "team_id": None,
            "call_type": "invoke",
        }
    ]


@pytest.mark.asyncio
async def test_each_run_uses_its_own_frozen_model_resilience_policy() -> None:
    objects = Objects()
    client = Client()
    captured = []

    def client_factory(_model, **kwargs):
        captured.append(kwargs["policy"])
        return client

    handler = ModelCallActivityHandler(
        objects=objects,
        models=Models(),
        tools=Catalog(),
        client_factory=client_factory,
    )
    for attempts in (2, 8):
        context = ActivityContext(
            worker_id="worker-1",
            claim_generation=1,
            idempotency_key=f"activity-{attempts}",
            owner_user_id="user-1",
            team_id=None,
            run=run_execution_context_for(
                "agent",
                policy=ExecutionPolicy(
                    model_resilience=ModelResiliencePolicy(max_attempts_per_call=attempts)
                ),
            ),
        )
        outcome = await handler.execute(
            request("model.call", input_payload={"allow_tools": True}),
            context,
        )
        assert outcome.status == "succeeded"

    assert [policy.max_attempts_per_call for policy in captured] == [2, 8]


@pytest.mark.asyncio
async def test_tool_activity_executes_exactly_persisted_tool_intent() -> None:
    objects = Objects()
    catalog = Catalog()
    handler = ToolCallActivityHandler(objects=objects, tools=catalog)

    outcome = await handler.execute(
        request(
            "tool.call",
            input_payload={
                "round": 0,
                "tool_call": {
                    "call_id": "call-9",
                    "name": "write_file",
                    "arguments": {
                        "filepath": "/work/report.md",
                        "content": "done",
                    },
                },
            },
        ),
        CONTEXT,
    )

    assert outcome.status == "succeeded"
    assert catalog.invocations[0][2:] == (
        "write_file",
        {"filepath": "/work/report.md", "content": "done"},
    )
    message = objects.written[0][1]["message"]
    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call-9"


@pytest.mark.asyncio
async def test_retrieval_activity_persists_citable_context() -> None:
    objects = Objects()
    memories = Memories()
    handler = RetrievalActivityHandler(
        objects=objects,
        tools=Catalog(),
        memories=memories,
    )

    outcome = await handler.execute(request("retrieval.search"), CONTEXT)

    assert outcome.status == "succeeded"
    assert memories.policy == CONTEXT.run.policy_snapshot.family_policy.memory
    assert objects.written[0][1] == {
        "kind": "retrieval",
        "message": {
            "role": "system",
            "content": json.dumps(
                {
                    "query": "Find the design and write a report",
                    "sources": [
                        {
                            "kind": "memory",
                            "content": "<long_term_memory>fact</long_term_memory>",
                        },
                        {"kind": "knowledge_base", "content": "design"},
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    }


@pytest.mark.asyncio
async def test_disabled_skill_degrades_model_call_and_flags_public_data() -> None:
    # P2-10：skill 被禁用时 model.call 与工具目录一致地降级为"无 skill 继续"，
    # 并通过 public_data 提示（实现取 warning 日志 + 提示字段，未走通知链）。
    from types import SimpleNamespace

    objects = Objects()
    objects.input["skill_id"] = "skill-9"
    client = Client()

    class _Skills:
        async def get_skill(self, skill_id, *, scope):
            assert skill_id == "skill-9"
            return SimpleNamespace(enabled=False)

    handler = ModelCallActivityHandler(
        objects=objects,
        models=Models(),
        tools=Catalog(),
        skills=_Skills(),
        client_factory=lambda *args, **kwargs: client,
    )

    outcome = await handler.execute(
        request(
            "model.call",
            input_payload={"allow_tools": True, "history_refs": [], "round": 0},
        ),
        CONTEXT,
    )

    assert outcome.status == "succeeded"
    assert outcome.public_data["skill_disabled"] is True
    system_prompt = client.calls[0][0][0]["content"]
    assert "skill" not in system_prompt.lower() or "OpenCitadel" in system_prompt


@pytest.mark.asyncio
async def test_model_call_reports_token_usage_progress_when_sink_present() -> None:
    # P2-12 最小接线：非流式调用完成后按终局用量上报一次 token 计数。
    objects = Objects()
    client = Client(usage={"prompt_tokens": 11, "completion_tokens": 7})
    reports: list[dict] = []

    async def report_progress(payload):
        reports.append(payload)
        return True

    context = CONTEXT.model_copy(update={"report_progress": report_progress})
    handler = ModelCallActivityHandler(
        objects=objects,
        models=Models(),
        tools=Catalog(),
        client_factory=lambda *args, **kwargs: client,
    )

    outcome = await handler.execute(
        request(
            "model.call",
            input_payload={"allow_tools": True, "history_refs": [], "round": 0},
        ),
        context,
    )

    assert outcome.status == "succeeded"
    assert reports == [{"kind": "model_usage", "prompt_tokens": 11, "completion_tokens": 7}]
