"""Typed evaluation requests never accept server provenance or object locations."""

from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue

from app.domain.evaluation.dataset import ConversationMessage, ImmutableModel, KnowledgeBinding


class MutationRequest(ImmutableModel):
    request_id: str = Field(min_length=1, max_length=255)
    expected_revision: int = Field(ge=0)


class CreateDatasetRequest(MutationRequest):
    name: str = Field(min_length=1, max_length=255)


class CaseInput(ImmutableModel):
    input: str | tuple[ConversationMessage, ...]
    history: tuple[ConversationMessage, ...] = ()
    attachments: tuple[str, ...] = ()
    knowledge_bindings: tuple[KnowledgeBinding, ...] = ()
    reference_answer: str | None = None
    reference_confirmed: bool = False
    input_confirmed: bool = False
    rules: tuple[dict[str, JsonValue], ...] = ()
    tags: tuple[str, ...] = ()
    applicable_dimensions: tuple[str, ...] = ()


class UpdateCaseRequest(MutationRequest):
    case: CaseInput


class ApplyImportRequest(MutationRequest):
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class FromRunRequest(MutationRequest):
    run_id: UUID
    step_id: str = Field(min_length=1)
    at: str = Field(min_length=1)
    case_key: str = Field(min_length=1, max_length=255)
    attachment_ids: tuple[str, ...] = ()
    knowledge_ids: tuple[str, ...] = ()


class FromRunPreviewRequest(ImmutableModel):
    expected_revision: int = Field(ge=1)
    run_id: UUID
    step_id: str = Field(min_length=1)
    at: str = Field(min_length=1)
    case_key: str = Field(min_length=1, max_length=255)


class AnalysisCertification(ImmutableModel):
    status: Literal["certified"]
