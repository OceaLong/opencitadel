"""Real SDK iterator wrapper with in-memory transport frames, no network."""

import asyncio
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionChunk

from app.application.ports.inference_dispatch import dispatch_context
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending", ["complete", "zero", "missing_usage", "partial", "cancel", "consumer"]
)
async def test_sdk_stream_closes_without_settling_incomplete_evidence(ending):
    receipts, closed = [], []

    class Guard:
        async def before_send(self, *args):
            return "physical"

        async def after_send(self, identity, usage, revision):
            receipts.append(usage)

        async def after_completion(self, *args):
            receipts.append(None)

    class Stream:
        async def close(self):
            closed.append(True)

        async def __aiter__(self):
            def chunk(choices, usage=None):
                return ChatCompletionChunk(
                    id="c",
                    created=0,
                    model="fixture",
                    object="chat.completion.chunk",
                    choices=choices,
                    usage=usage,
                )

            yield chunk([{"index": 0, "delta": {"content": "one"}}])
            if ending == "cancel":
                raise asyncio.CancelledError()
            if ending in {"partial", "consumer"}:
                return
            yield chunk([{"index": 0, "delta": {}, "finish_reason": "stop"}])
            if ending in {"complete", "zero"}:
                yield chunk(
                    [],
                    (
                        {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
                        if ending == "complete"
                        else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                    ),
                )

    async def send(**kwargs):
        return Stream()

    model = resolved_chat_model()
    llm = OpenAILLM(model)
    await llm._client.close()
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=send)))
    with dispatch_context(Guard(), model):
        if ending == "cancel":
            with pytest.raises(asyncio.CancelledError):
                _ = [x async for x in llm.stream_invoke([])]
        elif ending == "consumer":
            async with aclosing(llm.stream_invoke([])) as stream:
                await anext(stream)
        else:
            output = [x async for x in llm.stream_invoke([])]
            if ending == "zero":
                assert output[-1]["usage"]["total_tokens"] == 0
    assert closed == [True]
    assert len(receipts) == int(ending in {"complete", "zero", "missing_usage"})
    if ending == "missing_usage":
        assert receipts == [None]
    if ending == "complete":
        assert receipts[0]["total_tokens"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed", ["content_after_finish", "duplicate_finish", "duplicate_usage"]
)
async def test_actual_sdk_malformed_terminal_order_stays_unsettled(malformed):
    receipts = []

    class Guard:
        async def before_send(self, *a):
            return "physical"

        async def after_send(self, *a):
            receipts.append(a)

        async def after_completion(self, *a):
            receipts.append(a)

    class Stream:
        closed = False

        async def close(self):
            self.closed = True

        async def __aiter__(self):
            def chunk(delta=None, finish=None, usage=None):
                return ChatCompletionChunk(
                    id="c",
                    created=0,
                    model="fixture",
                    object="chat.completion.chunk",
                    choices=[]
                    if usage is not None
                    else [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
                    usage=usage,
                )

            yield chunk({"content": "one"}, "stop")
            if malformed == "content_after_finish":
                yield chunk({"content": "late"})
            if malformed == "duplicate_finish":
                yield chunk(finish="stop")
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            yield chunk(usage=usage)
            if malformed == "duplicate_usage":
                yield chunk(usage=usage)

    stream = Stream()

    async def send(**kwargs):
        return stream

    model = resolved_chat_model()
    llm = OpenAILLM(model)
    await llm._client.close()
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=send)))
    with (
        dispatch_context(Guard(), model),
        pytest.raises(
            ValueError,
            match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
        ),
    ):
        _ = [x async for x in llm.stream_invoke([])]
    assert stream.closed
    assert receipts == []
