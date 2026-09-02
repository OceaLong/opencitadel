"""Strict HTTP command payloads for the greenfield kernel."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        extra="forbid",
    )


class CreateRunRequest(ApiModel):
    command_id: UUID | None = None
    run_id: UUID | None = None
    prompt: str = Field(min_length=1, max_length=200_000)
    title: str = Field(default="", max_length=500)
    knowledge_version_ids: list[str] = Field(default_factory=list, max_length=100)


class PromptRunRequest(ApiModel):
    command_id: UUID | None = None
    prompt: str = Field(min_length=1, max_length=200_000)
    expected_stream_version: int | None = Field(default=None, ge=0)


class CancelRunRequest(ApiModel):
    command_id: UUID | None = None
    reason: str = Field(default="user_requested", min_length=1, max_length=500)
    expected_stream_version: int | None = Field(default=None, ge=0)


class DispositionAction(StrEnum):
    ARCHIVE = "archive"
    PURGE = "purge"


class DispositionCommandRequest(ApiModel):
    command_id: UUID | None = None
    plan_hash: str = Field(min_length=64, max_length=64)
    confirmation: str = Field(min_length=1, max_length=500)
    expected_stream_version: int | None = Field(default=None, ge=0)


class RestoreRunRequest(ApiModel):
    command_id: UUID | None = None
    expected_stream_version: int | None = Field(default=None, ge=0)


class ApprovalDecisionRequest(ApiModel):
    command_id: UUID | None = None
    decision: str = Field(pattern="^(approved|rejected)$")
    feedback: str = Field(default="", max_length=2_000)
    expected_stream_version: int | None = Field(default=None, ge=0)
