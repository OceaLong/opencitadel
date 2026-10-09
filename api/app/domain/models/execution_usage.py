"""Immutable accounting values. Missing evidence is never a zero."""

import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Purpose = Literal["production", "evaluation_subject", "evaluation_judge", "unknown"]


def price_usage(
    input_tokens, output_tokens, input_per_million, output_per_million
) -> Decimal | None:
    values = (input_tokens, output_tokens, input_per_million, output_per_million)
    for value in values:
        if value is not None and (
            isinstance(value, bool) or not Decimal(str(value)).is_finite() or value < 0
        ):
            raise ValueError("invalid usage or price")
    if any(value is None for value in values):
        return None
    return (
        Decimal(input_tokens) * Decimal(str(input_per_million))
        + Decimal(output_tokens) * Decimal(str(output_per_million))
    ) / Decimal(1_000_000)


def content_revision(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


class PriceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_read_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    reasoning_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    reasoning_uses_output_rate: bool = False
    provenance: Literal["legacy_positive", "explicit"] = "explicit"

    @property
    def revision(self):
        return content_revision(self.model_dump(mode="json"))

    def cost(self, usage):
        input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if input_tokens is None or output_tokens is None or usage.get("total_consistent") is False:
            return None
        cached, written, reasoning = (
            usage.get(k) for k in ("cached_tokens", "cache_write_tokens", "reasoning_tokens")
        )
        if input_tokens and cached is None:
            return None
        if usage.get("provider") == "anthropic" and input_tokens and written is None:
            return None
        states = usage.get("category_states", {})
        if input_tokens and written is None and states.get("cache_write_tokens") != "inapplicable":
            return None
        if (
            output_tokens
            and reasoning is None
            and states.get("reasoning_tokens") != "inapplicable"
            and not self.reasoning_uses_output_rate
        ):
            return None
        base_input = input_tokens - (cached or 0) - (written or 0)
        base_output = output_tokens - (reasoning or 0)
        if min(base_input, base_output) < 0:
            return None
        base = price_usage(base_input, base_output, self.input_per_million, self.output_per_million)
        if base is None:
            return None
        for count, rate in (
            (cached, self.cache_read_per_million),
            (written, self.cache_write_per_million),
            (
                reasoning,
                self.output_per_million
                if self.reasoning_uses_output_rate
                else self.reasoning_per_million,
            ),
        ):
            if count:
                if rate is None:
                    return None
                base += Decimal(count) * rate / Decimal(1_000_000)
        return base
