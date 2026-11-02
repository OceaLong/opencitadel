"""Bounded administrative inventories; no private Run payloads are exposed."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.execution.definition import DefinitionPin, Digest
from app.domain.execution.family import RunFamily


class DefinitionInfo(BaseModel):
    family: RunFamily
    definition_version: int
    manifest_digest: Digest
    created_at: datetime
    retired_at: datetime | None
    planner_version: str
    compiler_version: str
    is_head: bool
    head_revision: int | None
    head_digest: Digest | None


class DefinitionPage(BaseModel):
    items: list[DefinitionInfo] = Field(max_length=100)
    next_offset: int | None


class WorkerInfo(BaseModel):
    worker_id: str
    boot_id: str
    last_seen_at: datetime
    expires_at: datetime
    available: bool
    definitions: list[DefinitionPin]
    build_artifact_digest: Digest


class WorkerPage(BaseModel):
    items: list[WorkerInfo] = Field(max_length=100)
    next_offset: int | None


class DefinitionRequestResult(BaseModel):
    request_id: UUID
    status: Literal["pending", "completed"]
    result: dict[str, Any] | None
