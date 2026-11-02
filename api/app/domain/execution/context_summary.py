"""Summary facts are derived evidence; logical identity never includes a lease."""

from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from app.domain.execution.definition import DefinitionPin, DefinitionValue, Digest


class ContextCompactBinding(DefinitionValue):
    """Written by the planner into the formal Activity request, never HTTP input."""

    prepare_activity_id: UUID
    prepare_ref: str = Field(min_length=1, max_length=2048)
    compaction_digest: Digest
    chunk_digest: Digest
    round_index: int = Field(ge=0, le=1000)
    ordinal: int = Field(ge=0, le=127)


def summary_activity_key(binding: ContextCompactBinding) -> str:
    return f"context:compact:{binding.round_index}:{binding.ordinal}:{binding.chunk_digest}"


def logical_summary_identity(
    run_id: UUID, pin: DefinitionPin, *, round_index: int, ordinal: int, chunk_digest: str
) -> UUID:
    if (
        type(round_index) is not int
        or not 0 <= round_index <= 1000
        or type(ordinal) is not int
        or not 0 <= ordinal < 128
    ):
        raise ValueError("CONTEXT_SUMMARY_IDENTITY_INVALID")
    if len(chunk_digest) != 64 or any(char not in "0123456789abcdef" for char in chunk_digest):
        raise ValueError("CONTEXT_SUMMARY_IDENTITY_INVALID")
    return uuid5(
        run_id, f"context-summary:v1:{pin.manifest_digest}:{round_index}:{ordinal}:{chunk_digest}"
    )


class ContextSummaryFact(DefinitionValue):
    kind: Literal["fact", "constraint", "decision", "uncertainty"]
    text: str = Field(min_length=1, max_length=16384)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_sources(self):
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("CONTEXT_SUMMARY_FORMAT_INVALID")
        return self


class ContextSummary(DefinitionValue):
    format_version: Literal[1] = 1
    chunk_digest: Digest
    facts: tuple[ContextSummaryFact, ...] = Field(min_length=1, max_length=256)

    def require_chunk(self, chunk):
        allowed = {ref.source_id for ref in chunk.sources}
        if self.chunk_digest != chunk.digest or any(
            source not in allowed for fact in self.facts for source in fact.source_ids
        ):
            raise ValueError("CONTEXT_SUMMARY_SOURCE_MISMATCH")


class ContextSummaryProposal(DefinitionValue):
    format_version: Literal[1] = 1
    logical_id: UUID
    scope_key: str = Field(min_length=1, max_length=261)
    run_id: UUID
    activity_id: UUID
    request_generation: int = Field(ge=0)
    definition_pin: DefinitionPin
    round_index: int = Field(ge=0, le=1000)
    ordinal: int = Field(ge=0, le=127)
    input_digest: Digest
    chunk_digest: Digest
    purpose: Literal["production", "evaluation_subject", "evaluation_judge"]
    namespace_id: UUID | None = None
    physical_call_ids: tuple[UUID, ...] = Field(min_length=1, max_length=3)
    result_ref: str = Field(min_length=1, max_length=2048)
    result_digest: Digest

    @model_validator(mode="after")
    def proposal_identity(self):
        expected = logical_summary_identity(
            self.run_id,
            self.definition_pin,
            round_index=self.round_index,
            ordinal=self.ordinal,
            chunk_digest=self.chunk_digest,
        )
        if self.logical_id != expected:
            raise ValueError("CONTEXT_SUMMARY_IDENTITY_INVALID")
        if self.purpose.startswith("evaluation_") and self.namespace_id is None:
            raise ValueError("CONTEXT_SUMMARY_NAMESPACE_REQUIRED")
        if self.result_ref != f"execution/results/{self.activity_id}/{self.result_digest}.json":
            raise ValueError("CONTEXT_SUMMARY_RESULT_IDENTITY_INVALID")
        if len(set(self.physical_call_ids)) != len(self.physical_call_ids):
            raise ValueError("CONTEXT_SUMMARY_PHYSICAL_IDENTITY_INVALID")
        return self
