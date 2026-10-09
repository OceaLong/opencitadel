"""Pure handler contract checks; no provider/accounting integration evidence."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.application.execution.activities.model_call import ModelCallActivityHandler
from app.application.execution.progress import ActivityProgressRecord
from app.application.ports.inference_dispatch import current_dispatch
from tests.app.application.execution.test_conversation_activities import (
    CONTEXT,
    Catalog,
    Models,
    Objects,
    TokenUsage,
    request,
)
from tests.app.execution_test_support import run_execution_context_for


class StreamClient:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = self.stream_closed = self.invoked = 0
        self.contexts = []

    async def invoke(self, messages, tools=None):
        self.invoked += 1
        return {"content": "default", "tool_calls": []}

    async def stream_invoke(self, messages, tools=None):
        assert tools is None
        try:
            for chunk in self.chunks:
                self.contexts.append(current_dispatch.get())
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk
        finally:
            self.stream_closed += 1

    async def aclose(self):
        self.closed += 1


def setup(chunks, *, enabled=True, source="session", family="ask", ack=True):
    objects, usage, reports = Objects(), TokenUsage(), []
    client = StreamClient(chunks)
    run = run_execution_context_for(family).model_copy(update={"source_entity_type": source})

    async def progress(payload):
        # Validation used by the real worker, not a permissive list-append sink.
        record = ActivityProgressRecord(
            run_id=run.run_id,
            activity_id=request("model.call").activity_id,
            generation=0,
            claim_generation=1,
            sequence=len(reports) + 1,
            owner_user_id="user-1",
            team_id=None,
            occurred_at=datetime.now(UTC),
            **payload,
        )
        reports.append(record)
        return ack

    handler = ModelCallActivityHandler(
        objects=objects,
        models=Models(),
        tools=Catalog(),
        token_usage=usage,
        client_factory=lambda *a, **k: client,
        text_stream=enabled,
    )
    context = CONTEXT.model_copy(update={"run": run, "report_progress": progress})
    return handler, context, objects, client, usage, reports


@pytest.mark.asyncio
async def test_stream_exhausts_usage_preserves_output_and_safe_valid_counts():
    handler, context, objects, client, usage, reports = setup(
        [
            {"content": "private one", "reasoning_content": "secret"},
            {},
            {"content": " and two"},
            {"finish_reason": "stop"},
            {"usage": {"prompt_tokens": 11, "completion_tokens": 7}},
        ],
        ack=False,
    )
    result = await handler.execute(request("model.call"), context)
    assert result.status == "succeeded"  # display failure is not domain failure
    assert objects.written[0][1]["message"]["content"] == "private one and two"
    assert [r.message for r in reports] == [
        "Received fragments: 1",
        "Received fragments: 2",
        "Model response complete",
    ]
    assert [r.progress for r in reports] == [0, 0, 100]
    assert usage.records[0]["completion_tokens"] == 7
    assert len(client.contexts) == 5
    assert all(model is not None for _, model in client.contexts)
    assert client.closed == client.stream_closed == 1
    assert client.invoked == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks",
    [
        [{"content": "partial"}],
        [{"content": "partial"}, RuntimeError("transport")],
        [{"content": "partial"}, asyncio.CancelledError()],
        [{"tool_calls": [{"function": {"arguments": "private"}}]}],
        [{"content": "x" * (1024 * 1024 + 1)}],
        [{"content": "partial"}, {"finish_reason": "length"}],
    ],
)
async def test_incomplete_stream_never_writes_success_and_closes(chunks):
    handler, context, objects, client, usage, reports = setup(chunks)
    with pytest.raises((ValueError, RuntimeError, asyncio.CancelledError)):
        await handler.execute(request("model.call"), context)
    assert not objects.written
    assert not usage.records
    assert all(r.progress != 100 for r in reports)
    assert client.closed == client.stream_closed == 1
    assert client.invoked == 0


@pytest.mark.asyncio
async def test_missing_usage_stays_missing_after_complete_stream():
    handler, context, _objects, _client, usage, _reports = setup(
        [{"content": "ok"}, {"finish_reason": "stop"}]
    )
    assert (await handler.execute(request("model.call"), context)).status == "succeeded"
    assert not usage.records


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "source", "family", "tools"),
    [
        (False, "session", "ask", False),
        (True, "evaluation_isolated_case", "ask", False),
        (True, "session", "agent", False),
        (True, "session", "ask", True),
    ],
)
async def test_default_and_unsupported_paths_still_invoke(enabled, source, family, tools):
    handler, context, _objects, client, _usage, _reports = setup(
        [], enabled=enabled, source=source, family=family
    )
    assert (
        await handler.execute(request("model.call", input_payload={"allow_tools": tools}), context)
    ).status == "succeeded"
    assert client.invoked == client.closed == 1
    assert not client.stream_closed


@pytest.mark.asyncio
async def test_owned_client_closes_on_message_setup_failure():
    handler, context, objects, client, _, _ = setup([])
    objects.input["conversation"] = "invalid"
    with pytest.raises(TypeError):
        await handler.execute(request("model.call"), context)
    assert client.closed == 1


@pytest.mark.asyncio
async def test_unsupported_provider_keeps_invoke_path():
    from app.domain.models.inference import InferenceProvider

    handler, context, _objects, client, _, _ = setup([])

    class UnsupportedModels(Models):
        async def resolve_chat(self, *args, **kwargs):
            value = await super().resolve_chat(*args, **kwargs)
            return value.model_copy(
                update={
                    "endpoint": value.endpoint.model_copy(
                        update={"provider": InferenceProvider.GEMINI}
                    )
                }
            )

    handler._models = UnsupportedModels()
    assert (await handler.execute(request("model.call"), context)).status == "succeeded"
    assert client.invoked == 1


@pytest.mark.asyncio
async def test_close_error_does_not_replace_cancel_and_quota_rejection_closes():
    from app.domain.errors import TooManyRequestsError

    handler, context, _objects, client, _, _ = setup([asyncio.CancelledError()])

    async def close_error():
        client.closed += 1
        raise RuntimeError("close")

    client.aclose = close_error
    with pytest.raises(asyncio.CancelledError):
        await handler.execute(request("model.call"), context)
    assert client.closed == 1
    handler, context, _objects, client, _, _ = setup([])

    class Quota:
        async def check_model_call_budget(self, **kwargs):
            raise TooManyRequestsError("quota")

    handler._quota = Quota()
    assert (
        await handler.execute(request("model.call"), context)
    ).failure_code == "MODEL_CALL_BUDGET_EXCEEDED"
    assert client.closed == 1
    assert not client.invoked
