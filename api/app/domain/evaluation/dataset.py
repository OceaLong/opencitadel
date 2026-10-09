"""Immutable case revisions and publication validation shared by evaluation consumers."""

from collections.abc import Callable
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.domain.evaluation.rule_validation import validate_rule_definition
from app.domain.json_values import deep_freeze_json
from app.domain.models.resource_pin import ResourceIdentity

MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_IMPORT_CASES = 1000


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConversationMessage(ImmutableModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class KnowledgeBinding(ImmutableModel):
    resource_id: str = Field(min_length=1, max_length=255)
    version_id: str = Field(min_length=1, max_length=255)


class CaseRevision(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    case_key: str = Field(min_length=1, max_length=255)
    revision: int = Field(default=1, ge=1)
    input: str | tuple[ConversationMessage, ...]
    history: tuple[ConversationMessage, ...] = ()
    attachments: tuple[str, ...] = ()
    knowledge_bindings: tuple[KnowledgeBinding, ...] = ()
    reference_answer: str | None = None
    reference_confirmed: bool = False
    rules: tuple[dict[str, JsonValue], ...] = ()
    tags: tuple[str, ...] = ()
    applicable_dimensions: tuple[str, ...] = ()
    source_run_id: UUID | None = None
    source_step_id: str | None = None
    source_at: str | None = None
    source_content_id: str | None = None
    source_request: dict[str, JsonValue] = Field(default_factory=dict)
    input_status: Literal["provided", "admitted", "sanitized", "unavailable", "edited"] = "provided"
    input_confirmed: bool = False
    reference_candidate: str | None = None
    resources: tuple[ResourceIdentity, ...] = ()

    @field_validator("case_key", "input")
    @classmethod
    def nonempty(cls, value):
        if (isinstance(value, str) and not value.strip()) or not value:
            raise ValueError("empty value")
        return value

    @field_validator("source_request", mode="after")
    @classmethod
    def freeze_request(cls, value):
        return deep_freeze_json(value)

    @field_validator("rules", mode="after")
    @classmethod
    def freeze_rules(cls, value):
        return tuple(deep_freeze_json(rule) for rule in value)


class DatasetSummary(ImmutableModel):
    id: UUID
    name: str
    revision: int = Field(ge=1)
    case_count: int = Field(ge=0)
    version_count: int = Field(ge=0)


class DatasetDraft(ImmutableModel):
    id: UUID
    name: str
    revision: int = Field(ge=1)
    cases: tuple[CaseRevision, ...] = ()


class DatasetVersion(ImmutableModel):
    id: UUID
    dataset_id: UUID
    revision: int = Field(ge=1)
    cases: tuple[CaseRevision, ...]
    pins: tuple[ResourceIdentity, ...] = ()


def validate_case_keys(cases: list[dict]) -> None:
    keys = [case.get("case_key") for case in cases]
    if any(not isinstance(key, str) or not key.strip() for key in keys):
        raise ValueError("empty case_key")
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate case_key")
    if len(cases) > MAX_IMPORT_CASES:
        raise ValueError("case_limit_exceeded")


def validate_publication(
    cases: tuple[CaseRevision, ...] | list[CaseRevision],
    *,
    rule_validator: Callable[[dict], None] | None = None,
    reference_required: bool = False,
) -> None:
    """E07 can inject its pure rule validator; E02 supplies rubric reference policy."""
    validate_case_keys([{"case_key": case.case_key} for case in cases])
    if not cases:
        raise ValueError("empty_dataset")
    for case in cases:
        if case.source_run_id and (
            not case.input_confirmed or case.input_status in ("sanitized", "unavailable")
        ):
            raise ValueError("input_confirmation_required")
        if case.reference_answer is not None and not case.reference_confirmed:
            raise ValueError("reference_unconfirmed")
        needs_reference = reference_required or any(
            rule.get("reference_required") is True for rule in case.rules
        )
        if needs_reference and not case.reference_answer:
            raise ValueError("reference_required")
        for rule in case.rules:
            validate_rule_definition(rule)
            if rule_validator is not None:
                rule_validator(rule)
