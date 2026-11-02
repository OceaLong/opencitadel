"""Accepted, immutable supplied history; pages are bound by the Run input digest."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.execution.definition import DefinitionValue, Digest


class ContextInputPage(DefinitionValue):
    first: int = Field(ge=0, le=4095)
    count: int = Field(ge=1, le=200)
    object_ref: str = Field(min_length=1, max_length=2048)
    digest: Digest
    byte_length: int = Field(ge=1, le=65536)


class ContextInputCollection(DefinitionValue):
    format_version: Literal[1] = 1
    run_id: UUID
    scope_key: str = Field(min_length=1, max_length=261)
    count: int = Field(ge=1, le=4096)
    byte_length: int = Field(ge=1, le=8388608)
    pages: tuple[ContextInputPage, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def coverage(self):
        position = size = 0
        for page in self.pages:
            if (
                page.first != position
                or page.object_ref != f"execution/inputs/{self.run_id}/{page.digest}.json"
            ):
                raise ValueError("CONTEXT_COLLECTION_INDEX_INVALID")
            position += page.count
            size += page.byte_length
        if position != self.count or size != self.byte_length:
            raise ValueError("CONTEXT_COLLECTION_INDEX_INVALID")
        return self
