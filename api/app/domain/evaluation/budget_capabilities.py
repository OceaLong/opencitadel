"""Deployment attestations select code-reviewed provider ceilings, never token estimates.

These conservative profiles cover one direct native Messages/GenerateContent
request. They deliberately exclude server tools, remote files, and proxy retries.
The full supported input window is reserved even for an empty prompt.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.domain.evaluation.configuration import digest
from app.domain.evaluation.dataset import ImmutableModel
from app.domain.json_values import FrozenJsonDict
from app.domain.models.execution_usage import PriceSnapshot

# Verified native provider documentation on 2026-09-10. Adding/changing a bound
# requires a reviewed capability profile, not an HTTP model-setting override.
PROFILES = {
    "acceptance-chat-v1": (
        "openai",
        "acceptance-chat",
        "http://acceptance-inference:8080/v1",
        262144,
        4096,
        "fixture:acceptance-chat-v1",
    ),
    "openai-gpt41-chat-v1": (
        "openai",
        "gpt-4.1-2025-04-14",
        "https://api.openai.com/v1",
        1047576,
        32768,
        "https://developers.openai.com/api/docs/models/gpt-4.1",
    ),
    "anthropic-haiku45-messages-v1": (
        "anthropic",
        "claude-haiku-4-5-20251001",
        "https://api.anthropic.com",
        200000,
        64000,
        "https://platform.claude.com/docs/en/models/overview",
    ),
    "gemini25-flash-generate-content-v1": (
        "gemini",
        "gemini-2.5-flash",
        "https://generativelanguage.googleapis.com",
        1048576,
        65536,
        "https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash",
    ),
}


@dataclass(frozen=True)
class RequestBound:
    tokens: int
    money: Decimal | None
    source: str


class EndpointPool(ImmutableModel):
    provider: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    pool: str = Field(min_length=1)


class BudgetProfile(ImmutableModel):
    endpoint_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    profile: Literal[
        "acceptance-chat-v1",
        "anthropic-haiku45-messages-v1",
        "gemini25-flash-generate-content-v1",
        "openai-gpt41-chat-v1",
    ]
    price: PriceSnapshot | None = None

    @model_validator(mode="after")
    def identity(self):
        if self.model != PROFILES[self.profile][1]:
            raise ValueError("budget_profile_identity_mismatch")
        return self

    def bound(self, payload, *, output):
        provider, model, _, ceiling, maximum, source = PROFILES[self.profile]
        if type(output) is not int or not 0 < output <= maximum:
            raise ValueError("budget_output_bound_invalid")
        if self.profile == "acceptance-chat-v1":
            if (
                set(payload)
                - {
                    "model",
                    "messages",
                    "max_tokens",
                    "max_completion_tokens",
                    "tools",
                    "tool_choice",
                    "parallel_tool_calls",
                    "temperature",
                    "stream",
                    "stream_options",
                    "response_format",
                }
                or ("max_tokens" in payload and "max_completion_tokens" in payload)
                or payload.get("model") != model
                or payload.get("max_tokens", payload.get("max_completion_tokens")) != output
            ):
                raise ValueError("budget_payload_bound_mismatch")
            if (
                len(
                    json.dumps(
                        payload.get("messages", []), ensure_ascii=False, separators=(",", ":")
                    ).encode()
                )
                > 1048576
            ):
                raise ValueError("budget_input_mode_unsupported")
            price = self.price
            money = None
            if price is not None and all(
                rate is not None for rate in (price.input_per_million, price.output_per_million)
            ):
                money = (
                    ceiling * price.input_per_million + output * price.output_per_million
                ) / Decimal(1000000)
            return RequestBound(ceiling + output, money, source)
        if provider == "openai":
            allowed = {
                "model",
                "messages",
                "max_completion_tokens",
                "n",
                "tools",
                "tool_choice",
                "parallel_tool_calls",
                "temperature",
                "top_p",
                "stop",
                "stream",
                "stream_options",
                "response_format",
                "presence_penalty",
                "frequency_penalty",
                "logit_bias",
                "logprobs",
                "top_logprobs",
                "seed",
                "user",
                "timeout",
            }
            if (
                set(payload) - allowed
                or payload.get("model") != model
                or payload.get("max_completion_tokens") != output
                or type(payload.get("n", 1)) is not int
                or payload.get("n", 1) != 1
            ):
                raise ValueError("budget_payload_bound_mismatch")
            for tool in payload.get("tools", ()):
                if tool.get("type") != "function" or set(tool) - {"type", "function"}:
                    raise ValueError("budget_server_tools_forbidden")
            for message in payload.get("messages", ()):
                if set(message) - {"role", "content", "name", "tool_calls", "tool_call_id"}:
                    raise ValueError("budget_input_mode_unsupported")
                content = message.get("content")
                if (
                    content is not None
                    and not isinstance(content, str)
                    and (
                        not isinstance(content, (list, tuple))
                        or any(
                            part.get("type") != "text" or set(part) - {"type", "text"}
                            for part in content
                        )
                    )
                ):
                    raise ValueError("budget_input_mode_unsupported")
        elif provider == "anthropic":
            allowed = {
                "model",
                "messages",
                "system",
                "max_tokens",
                "tools",
                "tool_choice",
                "temperature",
                "top_p",
                "top_k",
                "stop_sequences",
                "stream",
                "thinking",
                "metadata",
            }
            if (
                set(payload) - allowed
                or payload.get("model") != model
                or payload.get("max_tokens") != output
            ):
                raise ValueError("budget_payload_bound_mismatch")
            if any(
                set(t) - {"name", "description", "input_schema", "cache_control"}
                for t in payload.get("tools", ())
            ):
                raise ValueError("budget_server_tools_forbidden")
            for message in [*payload.get("messages", ()), {"content": payload.get("system", "")}]:
                self._anthropic_content(message.get("content"))
        else:
            if set(payload) - {
                "contents",
                "generationConfig",
                "tools",
                "systemInstruction",
                "toolConfig",
                "safetySettings",
            }:
                raise ValueError("budget_payload_bound_mismatch")
            config = payload.get("generationConfig", {})
            if (
                set(config)
                - {
                    "temperature",
                    "maxOutputTokens",
                    "topP",
                    "topK",
                    "responseMimeType",
                    "responseSchema",
                    "thinkingConfig",
                    "seed",
                }
                or config.get("maxOutputTokens") != output
            ):
                raise ValueError("budget_payload_bound_mismatch")
            if any(set(t) - {"functionDeclarations"} for t in payload.get("tools", ())):
                raise ValueError("budget_server_tools_forbidden")
            for message in [*payload.get("contents", ()), payload.get("systemInstruction", {})]:
                for part in message.get("parts", ()):
                    if set(part) - {
                        "text",
                        "functionCall",
                        "functionResponse",
                        "thought",
                        "thoughtSignature",
                    }:
                        raise ValueError("budget_input_mode_unsupported")
        money = None
        if self.price is not None:
            price = self.price
            rates = [price.input_per_million, price.cache_read_per_million]
            if provider == "anthropic":
                rates.append(price.cache_write_per_million)
            output_rates = [
                price.output_per_million,
                price.output_per_million
                if price.reasoning_uses_output_rate
                else price.reasoning_per_million,
            ]
            if all(v is not None for v in [*rates, *output_rates]):
                money = (ceiling * max(rates) + output * max(output_rates)) / Decimal(1000000)
        return RequestBound(ceiling + output, money, source)

    @classmethod
    def _anthropic_content(cls, content):
        if isinstance(content, str) or content is None:
            return
        if not isinstance(content, (list, tuple)):
            raise ValueError("budget_input_mode_unsupported")  # noqa: TRY004 - unsupported wire mode
        for block in content:
            if block.get("type") not in {
                "text",
                "tool_use",
                "tool_result",
                "thinking",
                "redacted_thinking",
            }:
                raise ValueError("budget_input_mode_unsupported")
            if block.get("type") == "tool_result":
                cls._anthropic_content(block.get("content"))
            if block.get("cache_control", {}).get("ttl", "5m") not in {"5m", "1h"}:
                raise ValueError("budget_cache_mode_unsupported")


class _FrozenEndpointMap(FrozenJsonDict):
    def __deepcopy__(self, memo):
        # Values are themselves frozen EndpointPool instances.
        return self


class BudgetInventory(ImmutableModel):
    revision: str = Field(min_length=1)
    acceptance_fixture: bool = False
    endpoints: dict[str, EndpointPool] = Field(default_factory=dict)
    profiles: tuple[BudgetProfile, ...] = ()

    @field_validator("endpoints")
    @classmethod
    def freeze_endpoints(cls, value):
        return _FrozenEndpointMap(value)

    @model_validator(mode="after")
    def profiles_are_registered(self):
        seen = set()
        for profile in self.profiles:
            if profile.profile == "acceptance-chat-v1" and not self.acceptance_fixture:
                raise ValueError("acceptance_profile_disabled")
            endpoint = self.endpoints.get(profile.endpoint_id)
            provider, _, origin, *_ = PROFILES[profile.profile]
            key = (profile.endpoint_id, profile.model)
            if (
                key in seen
                or endpoint is None
                or endpoint.provider != provider
                or endpoint.origin != origin
            ):
                raise ValueError("budget_profile_identity_mismatch")
            seen.add(key)
        return self

    @property
    def fingerprint(self):
        # Adding the opt-in fixture must not invalidate accepted native proofs.
        body = self.model_dump(mode="json")
        if not self.acceptance_fixture:
            body.pop("acceptance_fixture")
        return digest(body)

    def endpoint_for(self, identity):
        endpoint = self.endpoints.get(identity["endpoint_id"])
        if (
            endpoint is None
            and self.acceptance_fixture
            and identity["provider"] == "openai"
            and identity["configured_model"] == "acceptance-chat"
            and identity["endpoint_digest"] == digest("http://acceptance-inference:8080/v1")
        ):
            return EndpointPool(
                provider="openai",
                origin="http://acceptance-inference:8080/v1",
                pool="acceptance-fixture-v1",
            )
        return endpoint

    def resolve(self, endpoint_id, provider, model, origin, *, configured_price=None):
        if self.acceptance_fixture and (provider, model, origin) == (
            "openai",
            "acceptance-chat",
            "http://acceptance-inference:8080/v1",
        ):
            # Preserve the configured accounting control in both the reservation
            # and usage evidence. Only an entirely unpriced fixture is free.
            configured = configured_price or {}
            input_rate, output_rate = configured.get("input"), configured.get("output")
            if input_rate is None and output_rate is None:
                input_rate = output_rate = Decimal(0)
            return BudgetProfile(
                endpoint_id=endpoint_id,
                model=model,
                profile="acceptance-chat-v1",
                price=PriceSnapshot(
                    input_per_million=input_rate,
                    output_per_million=output_rate,
                    cache_read_per_million=Decimal(0),
                    cache_write_per_million=Decimal(0),
                    reasoning_per_million=Decimal(0),
                ),
            )
        endpoint = self.endpoints.get(endpoint_id)
        if (
            endpoint is None
            or endpoint.provider != provider
            or endpoint.origin.rstrip("/") != origin.rstrip("/")
        ):
            raise ValueError("budget_endpoint_identity_mismatch")
        for profile in self.profiles:
            if profile.endpoint_id == endpoint_id and profile.model == model:
                return profile
        raise ValueError("budget_capability_unavailable")
