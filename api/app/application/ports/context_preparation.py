"""Materialization is bounded private I/O; compilation and summaries stay separate."""

from typing import Protocol

from pydantic import Field, field_validator

from app.domain.execution.context_models import (
    ContextBoundary,
    ContextCandidateProfile,
    ContextSource,
    ContextSourceRef,
)
from app.domain.execution.definition import DefinitionPin, DefinitionValue, Digest
from app.domain.execution.tool_contract import RecordedContract
from app.domain.json_values import JsonValue, deep_freeze_json
from app.domain.runtime_policy.context import ContextPolicy


class ContextCompilerInput(DefinitionValue):
    pin: DefinitionPin
    boundary: ContextBoundary
    policy: ContextPolicy
    candidates: tuple[ContextCandidateProfile, ...]
    summary_candidates: tuple[ContextCandidateProfile, ...]
    sources: tuple[ContextSource, ...]
    unavailable_sources: tuple[ContextSourceRef, ...] = ()
    tools: tuple[dict[str, JsonValue], ...] = ()
    tool_contract_digests: tuple[Digest, ...] = ()
    tool_contracts: tuple[RecordedContract, ...] = ()
    compactions_used: int = Field(default=0, ge=0, le=128)
    original_lookups: tuple[dict[str, JsonValue], ...] = Field(default=(), max_length=4)

    @field_validator("tools", "original_lookups")
    @classmethod
    def freeze_tools(cls, value):
        return tuple(deep_freeze_json(item) for item in value)


class ContextPreparationPort(Protocol):
    async def materialize(self, request, context, payload, *, definition) -> ContextCompilerInput:
        """Read fixed, authorized sources and tool contracts without inference."""
        ...
