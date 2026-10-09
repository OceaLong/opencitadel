import asyncio
from types import SimpleNamespace

import pytest

from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model


@pytest.mark.parametrize(
    ("model_name", "base_url", "profile_name", "token_field"),
    [
        (
            "gpt-4.1-2025-04-14",
            "https://api.openai.com/v1",
            "openai-gpt41-chat-v1",
            "max_completion_tokens",
        ),
        (
            "acceptance-chat",
            "http://acceptance-inference:8080/v1",
            "acceptance-chat-v1",
            "max_tokens",
        ),
    ],
)
def test_native_openai_actual_sdk_payload_is_guarded_before_transport(
    model_name, base_url, profile_name, token_field
):
    from app.application.evaluation.budget_candidates import (
        BudgetPayloadGuard,
        FrozenBudgetCandidates,
    )
    from app.application.evaluation.budget_service import BudgetAuthority
    from app.application.ports.inference_dispatch import dispatch_context
    from app.domain.errors import ServerRequestsError
    from app.domain.evaluation.budget_capabilities import BudgetInventory
    from app.domain.evaluation.configuration import digest
    from app.infrastructure.external.llm.openai_llm import OpenAILLM

    model = resolved_chat_model(model_name=model_name, base_url=base_url, max_output_tokens=100)
    inventory = BudgetInventory.model_validate(
        {
            "revision": "native",
            "acceptance_fixture": profile_name == "acceptance-chat-v1",
            "endpoints": {
                model.endpoint.id: {
                    "provider": "openai",
                    "origin": model.base_url,
                    "pool": "account",
                }
            },
            "profiles": [
                {
                    "endpoint_id": model.endpoint.id,
                    "model": model.model_name,
                    "profile": profile_name,
                }
            ],
        }
    )
    metadata = {
        "identity": {
            "model_id": model.id,
            "endpoint_id": model.endpoint.id,
            "provider": "openai",
            "configured_model": model.model_name,
            "endpoint_digest": digest(model.base_url),
        },
        "settings": model.model.settings.model_dump(mode="json"),
        "base_settings": model.model.settings.model_dump(mode="json"),
        "capabilities": model.capabilities.model_dump(mode="json"),
    }
    metadata.update(BudgetAuthority(inventory).configuration(metadata))
    authority = FrozenBudgetCandidates(
        inventory, {"inventory": inventory.fingerprint, "candidates": [metadata]}
    )
    sent, guarded, settled = [], [], []

    class Downstream:
        async def before_send(self, model, payload):
            guarded.append(dict(payload))
            return "physical"

        async def after_send(self, identity, usage, revision):
            settled.append(identity)

    async def create(**payload):
        assert guarded[-1] == {key: value for key, value in payload.items() if key != "timeout"}
        assert "timeout" in payload
        sent.append(payload)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        model_dump=lambda: {"role": "assistant", "content": "ok"}
                    ),
                )
            ],
            usage=None,
        )

    llm = OpenAILLM(model)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def run():
        with dispatch_context(BudgetPayloadGuard(authority, Downstream()), model):
            assert (await llm.invoke([{"role": "user", "content": "hello"}]))["content"] == "ok"
            llm._extra_params = {"thinking_request_params": {"max_completion_tokens": 101}}
            llm._thinking_enabled = True
            with pytest.raises(ServerRequestsError, match="budget_payload_bound_mismatch"):
                await llm.invoke([{"role": "user", "content": "hello"}])

    asyncio.run(run())
    assert len(sent) == len(guarded) == len(settled) == 1
    assert sent[0][token_field] == 100
    assert "timeout" not in guarded[0]


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
def test_native_http_final_payload_includes_tools_and_rejects_changed_bound(provider):
    import json

    import httpx

    from app.application.evaluation.budget_candidates import (
        BudgetPayloadGuard,
        FrozenBudgetCandidates,
    )
    from app.application.evaluation.budget_service import BudgetAuthority
    from app.application.ports.inference_dispatch import dispatch_context
    from app.domain.evaluation.budget_capabilities import PROFILES, BudgetInventory
    from app.domain.evaluation.configuration import digest
    from app.domain.models.inference import InferenceProvider
    from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
    from app.infrastructure.external.llm.gemini_llm import GeminiLLM

    profile_name = next(key for key, value in PROFILES.items() if value[0] == provider)
    _, wire_model, origin, *_ = PROFILES[profile_name]
    model = resolved_chat_model(
        provider=InferenceProvider(provider),
        model_name=wire_model,
        base_url=origin,
        max_output_tokens=100,
    )
    inventory = BudgetInventory.model_validate(
        {
            "revision": "native-http",
            "endpoints": {
                model.endpoint.id: {"provider": provider, "origin": origin, "pool": "account"}
            },
            "profiles": [
                {"endpoint_id": model.endpoint.id, "model": wire_model, "profile": profile_name}
            ],
        }
    )
    metadata = {
        "identity": {
            "model_id": model.id,
            "endpoint_id": model.endpoint.id,
            "provider": provider,
            "configured_model": wire_model,
            "endpoint_digest": digest(origin),
        },
        "settings": model.model.settings.model_dump(mode="json"),
        "base_settings": model.model.settings.model_dump(mode="json"),
        "capabilities": model.capabilities.model_dump(mode="json"),
    }
    metadata.update(BudgetAuthority(inventory).configuration(metadata))
    authority = FrozenBudgetCandidates(
        inventory, {"inventory": inventory.fingerprint, "candidates": [metadata]}
    )
    guarded, sent = [], []

    class Downstream:
        async def before_send(self, model, payload):
            guarded.append(payload)
            return "physical"

        async def after_send(self, identity, usage, revision):
            assert identity == "physical"

    async def transport(request):
        body = json.loads(request.content)
        assert body == guarded[-1]
        sent.append(body)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "ok"}]}
            if provider == "anthropic"
            else {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        )

    async def run():
        llm = (AnthropicLLM if provider == "anthropic" else GeminiLLM)(model)
        await llm._client.aclose()
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            llm._client = client
            with dispatch_context(BudgetPayloadGuard(authority, Downstream()), model):
                result = await llm.invoke(
                    [
                        {"role": "system", "content": "instructions"},
                        {"role": "user", "content": "hi"},
                    ],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "description": "test",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                )
                assert result["content"] == "ok"
                llm._max_tokens = 101
                with pytest.raises(ValueError, match="budget_payload_bound_mismatch"):
                    await llm.invoke([{"role": "user", "content": "hi"}])

    asyncio.run(run())
    assert len(sent) == len(guarded) == 1
    assert "tools" in sent[0]
