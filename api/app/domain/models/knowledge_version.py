#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Immutable knowledge-base version, revision, and manifest values."""
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, FrozenMapping):
        return value
    if isinstance(value, Mapping):
        return FrozenMapping(
            (key, _deep_freeze(nested))
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(item) for item in value)
    return value


def mutable_json_value(value: Any) -> Any:
    """Return JSON-native defensive data at a persistence boundary."""
    if isinstance(value, Mapping):
        return {
            key: mutable_json_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [mutable_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [mutable_json_value(item) for item in value]
    return value


class FrozenMapping(Mapping):
    """Recursively immutable mapping without a mutable ``dict`` base."""

    __slots__ = ("_data",)

    def __init__(self, source: Any = (), /, **kwargs: Any) -> None:
        mutable = dict(source, **kwargs)
        object.__setattr__(
            self,
            "_data",
            MappingProxyType(
                {
                    key: _deep_freeze(value)
                    for key, value in mutable.items()
                }
            ),
        )

    def __getitem__(self, key: Any) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return repr(dict(self._data))

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Mapping):
            return False
        return dict(self.items()) == dict(other.items())

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise TypeError("FrozenMapping is immutable")

    def __ior__(self, _other: Any):
        raise TypeError("FrozenMapping is immutable")

    def __copy__(self) -> "FrozenMapping":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "FrozenMapping":
        return self


class KnowledgeVersionState(str, Enum):
    BUILDING = "building"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


class DocumentRevisionState(str, Enum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class KnowledgeBaseVersion(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    knowledge_base_id: str
    parent_version_id: Optional[str] = None
    build_id: Optional[str] = None
    state: KnowledgeVersionState = KnowledgeVersionState.BUILDING
    capabilities: FrozenMapping = Field(default_factory=FrozenMapping)
    degraded_reasons: tuple[str, ...] = ()
    metrics: FrozenMapping = Field(default_factory=FrozenMapping)
    legacy_snapshot: bool = False
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    published_at: Optional[datetime] = None

    @field_validator("capabilities", "metrics", mode="before")
    @classmethod
    def _freeze_mappings(cls, value: Any) -> FrozenMapping:
        return FrozenMapping(value or {})

    @field_serializer("capabilities", "metrics")
    def _serialize_mappings(self, value: FrozenMapping) -> dict[str, Any]:
        return mutable_json_value(value)

    @field_validator("degraded_reasons", mode="before")
    @classmethod
    def _freeze_degraded_reasons(cls, value: Any) -> tuple[str, ...]:
        return tuple(value or ())


class KnowledgeDocumentRevision(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str
    source_ref: str = ""
    source_digest: str
    parsed_blocks: tuple[Any, ...] = ()
    page_count: int = 0
    state: DocumentRevisionState = DocumentRevisionState.UPLOADED
    # c7 imported already-indexed legacy rows without durable parse blocks.
    # The marker belongs to the immutable content revision (rather than one
    # version generation) so every descendant manifest that reuses the exact
    # revision can safely clone its durable chunks without regressing state.
    needs_chunk_clone: bool = False
    error: Optional[str] = None
    warning: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("parsed_blocks", mode="before")
    @classmethod
    def _freeze_parsed_blocks(cls, value: Any) -> tuple[Any, ...]:
        return tuple(_deep_freeze(item) for item in (value or ()))

    @field_serializer("parsed_blocks")
    def _serialize_parsed_blocks(self, value: tuple[Any, ...]) -> list[Any]:
        return mutable_json_value(value)


class KnowledgeVersionDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    version_id: str
    document_id: str
    document_revision_id: str
    ordinal: int
    state: DocumentRevisionState = DocumentRevisionState.UPLOADED
    error: Optional[str] = None
    warning: Optional[str] = None
