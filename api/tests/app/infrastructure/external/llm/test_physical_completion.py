from types import SimpleNamespace

import httpx
import pytest

from app.application.ports.inference_dispatch import dispatch_context
from app.domain.errors import ServerRequestsError
from app.domain.models.inference import InferenceProvider
from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model


@pytest.mark.asyncio
async def test_response_without_request_cannot_finalize_native_rejection():
    from app.infrastructure.external.llm.dispatch import physical_send

    model = resolved_chat_model(
        provider=InferenceProvider.ANTHROPIC, base_url="https://api.anthropic.com"
    )
    completed = []

    class Guard:
        async def before_send(self, *args):
            return "receipt"

        async def after_completion(self, *args):
            completed.append(args)

    async def send():
        return httpx.Response(400, json={"error": {"message": "rejection"}})

    with dispatch_context(Guard(), model):
        response = await physical_send(send, {}, provider="anthropic")
    assert response.status_code == 400
    assert completed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
@pytest.mark.parametrize("status", [429, 500, 408])
async def test_actual_http_adapters_do_not_finalize_uncertain_error_responses(provider, status):
    from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
    from app.infrastructure.external.llm.gemini_llm import GeminiLLM

    origin = (
        "https://api.anthropic.com"
        if provider == "anthropic"
        else "https://generativelanguage.googleapis.com"
    )
    model = resolved_chat_model(
        provider=InferenceProvider(provider),
        base_url=origin,
        model_name="claude-haiku-4-5-20251001" if provider == "anthropic" else "gemini-2.5-flash",
    )
    completed = []

    class Guard:
        async def before_send(self, *args):
            return "receipt"

        async def after_send(self, *args):
            raise AssertionError("error response is not a complete usage statement")

        async def after_completion(self, *args):
            completed.append(args)

    async def post(url, **kwargs):
        return httpx.Response(
            status,
            request=httpx.Request("POST", url),
            json={"error": {"message": "fake rejection"}},
        )

    adapter = AnthropicLLM(model) if provider == "anthropic" else GeminiLLM(model)
    original = adapter._client
    adapter._client = SimpleNamespace(post=post)
    try:
        with (
            dispatch_context(Guard(), model),
            pytest.raises((httpx.HTTPStatusError, ServerRequestsError)),
        ):
            await adapter.invoke([{"role": "user", "content": "hello"}])
    finally:
        await original.aclose()
    assert len(completed) == ((2 if provider == "anthropic" else 1) if status == 429 else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 400, 408, 409, 499, 500, 504])
@pytest.mark.parametrize("origin", ["https://api.openai.com/v1", "https://gateway.example/v1"])
async def test_sdk_status_error_only_finalizes_verified_native_rejection(status, origin):
    from openai import APIStatusError

    from app.infrastructure.external.llm.dispatch import physical_send

    model = resolved_chat_model(base_url=origin)
    completed = []

    class Guard:
        async def before_send(self, *args):
            return "receipt"

        async def after_completion(self, *args):
            completed.append(args)

    async def send():
        raise APIStatusError(
            "fake rejection",
            response=httpx.Response(
                status, request=httpx.Request("POST", origin + "/chat/completions")
            ),
            body={},
        )

    with dispatch_context(Guard(), model), pytest.raises(APIStatusError):
        await physical_send(send, {}, provider="openai")
    assert len(completed) == int(origin == "https://api.openai.com/v1" and status in (400, 429))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
@pytest.mark.parametrize("ending", ["complete", "partial", "cancel", "early_eof"])
async def test_actual_http_streams_require_terminal_and_eof_before_final_accounting(
    provider, ending
):
    import asyncio
    import json

    from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
    from app.infrastructure.external.llm.gemini_llm import GeminiLLM

    origin = (
        "https://api.anthropic.com"
        if provider == "anthropic"
        else "https://generativelanguage.googleapis.com"
    )
    model = resolved_chat_model(provider=InferenceProvider(provider), base_url=origin)
    facts = []

    class Guard:
        async def before_send(self, *args):
            return "receipt"

        async def after_send(self, identity, usage, revision):
            facts.append(usage)

        async def after_completion(self, *args):
            facts.append(None)

    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            if provider == "anthropic":
                first = {
                    "type": "message_start",
                    "message": {
                        "model": "native",
                        "usage": {
                            "input_tokens": 4,
                            "output_tokens": 0,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    },
                }
                final = {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 2},
                }
                terminal = {"type": "message_stop"}
            else:
                first = {
                    "candidates": [{"content": {"parts": [{"text": "hello"}]}}],
                    "usageMetadata": {
                        "promptTokenCount": 4,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": 5,
                    },
                }
                final = {
                    "candidates": [{"finishReason": "STOP", "content": {"parts": []}}],
                    "usageMetadata": {
                        "promptTokenCount": 4,
                        "candidatesTokenCount": 2,
                        "totalTokenCount": 6,
                    },
                }
                terminal = {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]}
            yield ("data: " + json.dumps(first) + "\n\n").encode()
            assert facts == []
            if ending == "cancel":
                raise asyncio.CancelledError()
            if ending == "early_eof":
                return
            if ending == "complete":
                yield ("data: " + json.dumps(final) + "\n\n").encode()
            yield ("data: " + json.dumps(terminal) + "\n\n").encode()
            assert facts == []

    def transport(request):
        return httpx.Response(200, stream=Bytes())

    adapter = AnthropicLLM(model) if provider == "anthropic" else GeminiLLM(model)
    original = adapter._client
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        with dispatch_context(Guard(), model):
            if ending == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    async for _ in adapter.stream_invoke([{"role": "user", "content": "hello"}]):
                        pass
            else:
                async for _ in adapter.stream_invoke([{"role": "user", "content": "hello"}]):
                    pass
    finally:
        await original.aclose()
        await adapter._client.aclose()
    assert len(facts) == int(ending in ("complete", "partial"))
    if ending == "partial":
        assert facts == [None]
    if ending == "complete":
        assert facts[0]["total_tokens"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending", ["complete", "early_eof", "duplicate_index", "invalid_index", "cancel"]
)
@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_multiple_requested_choices_require_all_terminal_evidence(ending, provider):
    import asyncio

    from app.infrastructure.external.llm.dispatch import physical_send

    model = resolved_chat_model()
    facts = []

    class Guard:
        async def before_send(self, *args):
            return "receipt"

        async def after_completion(self, *args):
            facts.append(args)

    def chunk(index):
        return (
            {"choices": [{"index": index, "finish_reason": "stop"}]}
            if provider == "openai"
            else {"candidates": [{"index": index, "finishReason": "STOP"}]}
        )

    async def chunks():
        yield chunk(0)
        if ending == "cancel":
            raise asyncio.CancelledError()
        if ending != "early_eof":
            yield chunk({"complete": 1, "duplicate_index": 0, "invalid_index": 2}[ending])

    async def send():
        return chunks()

    with dispatch_context(Guard(), model):
        stream = await physical_send(
            send, {"n": 2, "generationConfig": {"candidateCount": 2}}, provider=provider
        )
        if ending == "cancel":
            with pytest.raises(asyncio.CancelledError):
                async for _ in stream:
                    pass
        else:
            async for _ in stream:
                pass
    assert len(facts) == int(ending == "complete")
