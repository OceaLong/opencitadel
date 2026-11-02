"""A checkpoint contains completed summary evidence and its original closure."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.execution.context_models import ContextBoundary, ContextSourceRef, context_digest
from app.domain.execution.definition import DefinitionPin, DefinitionValue, Digest


class CheckpointSummary(DefinitionValue):
    activity_id: UUID
    result_ref: str = Field(min_length=1, max_length=2048)
    result_digest: Digest
    chunk_digest: Digest
    original_closure: tuple[ContextSourceRef, ...] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def reference(self):
        if self.result_ref != f"execution/results/{self.activity_id}/{self.result_digest}.json":
            raise ValueError("CONTEXT_CHECKPOINT_RESULT_MISMATCH")
        return self


class ContextCheckpoint(DefinitionValue):
    format_version: Literal[1] = 1
    run_id: UUID
    prepare_activity_id: UUID
    definition_pin: DefinitionPin
    boundary: ContextBoundary
    compaction_digest: Digest
    summaries: tuple[CheckpointSummary, ...] = Field(min_length=1, max_length=128)
    original_closure: tuple[ContextSourceRef, ...] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def closure(self):
        if len({item.activity_id for item in self.summaries}) != len(self.summaries):
            raise ValueError("CONTEXT_CHECKPOINT_RESULT_MISMATCH")
        if len({item.source_id for item in self.original_closure}) != len(self.original_closure):
            raise ValueError("CONTEXT_CHECKPOINT_SOURCE_MISMATCH")
        if any(item.scope_key != self.boundary.scope_key for item in self.original_closure):
            raise ValueError("CONTEXT_CHECKPOINT_SCOPE_MISMATCH")
        closure = {item.source_id: item for item in self.original_closure}
        nested = {
            item.source_id: item for summary in self.summaries for item in summary.original_closure
        }
        if nested != closure:
            raise ValueError("CONTEXT_CHECKPOINT_SOURCE_MISMATCH")
        return self

    @property
    def digest(self):
        return context_digest(self.model_dump(mode="json"))
