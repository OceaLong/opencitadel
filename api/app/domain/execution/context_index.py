"""Digest-bound UTF-8 spans for large private Activity results."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.domain.execution.context_resources import ContextResourceEvidence
from app.domain.execution.definition import DefinitionValue, Digest
from app.domain.json_values import JsonValue, deep_freeze_json


class OriginalContentChunk(DefinitionValue):
    offset: int = Field(ge=0)
    byte_length: int = Field(ge=1, le=65536)
    digest: Digest
    object_ref: str = Field(min_length=1, max_length=2048)


class ContextDocumentIndex(DefinitionValue):
    format_version: Literal[1] = 1
    activity_id: UUID
    digest: Digest
    byte_length: int = Field(ge=1, le=8388608)
    encoding: Literal["json", "zlib-json-v1"] = "json"
    stored_bytes: int | None = Field(default=None, ge=1, le=8388608)
    chunks: tuple[OriginalContentChunk, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def contiguous(self):
        offset = 0
        for chunk in self.chunks:
            if chunk.offset != offset or chunk.object_ref != (
                f"execution/context_documents/{self.activity_id}/{chunk.digest}.bin"
            ):
                raise ValueError("CONTEXT_DOCUMENT_INDEX_INVALID")
            offset += chunk.byte_length
        if offset != (self.stored_bytes or self.byte_length):
            raise ValueError("CONTEXT_DOCUMENT_INDEX_INVALID")
        if self.encoding == "json" and offset != self.byte_length:
            raise ValueError("CONTEXT_DOCUMENT_INDEX_INVALID")
        if self.encoding != "json" and self.stored_bytes is None:
            raise ValueError("CONTEXT_DOCUMENT_INDEX_INVALID")
        return self


class OriginalContentIndex(DefinitionValue):
    format_version: Literal[1] = 1
    scope_key: str = Field(min_length=1, max_length=261)
    run_id: UUID
    activity_id: UUID
    original_ref: str = Field(min_length=1, max_length=2048)
    original_digest: Digest
    original_bytes: int = Field(ge=1, le=8388608)
    content_digest: Digest
    content_bytes: int = Field(ge=0, le=8388608)
    message_metadata: dict[str, JsonValue]
    resource_evidence: ContextResourceEvidence | None = None
    chunks: tuple[OriginalContentChunk, ...] = Field(max_length=256)

    @field_validator("message_metadata")
    @classmethod
    def freeze_metadata(cls, value):
        return deep_freeze_json(value)

    @model_validator(mode="after")
    def span_integrity(self):
        prefix = f"execution/context_sources/{self.run_id}/{self.activity_id}/"
        if self.original_ref != f"execution/results/{self.activity_id}/{self.original_digest}.json":
            raise ValueError("original context reference identity mismatch")
        offset = 0
        for chunk in self.chunks:
            if chunk.offset != offset or chunk.object_ref != f"{prefix}{chunk.digest}.txt":
                raise ValueError(
                    "context chunk index is not contiguous or belongs to another source"
                )
            offset += chunk.byte_length
        if offset != self.content_bytes:
            raise ValueError("context chunk index does not cover its original content")
        if self.original_bytes < self.content_bytes:
            raise ValueError("context original byte count is smaller than its content")
        if "content" in self.message_metadata:
            raise ValueError("context message metadata cannot substitute original content")
        return self
