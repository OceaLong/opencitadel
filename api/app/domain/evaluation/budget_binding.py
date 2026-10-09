"""Private durable budget control identities, not public scheduler/Batch DTOs."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.json_values import deep_freeze_json


class BudgetNamespace(ImmutableModel):
    id: UUID
    suite_version_id: UUID
    suite_fingerprint: str
    requester: dict
    token_budget: Annotated[StrictInt, Field(gt=0)]
    money_budget: Decimal | None
    case_ids: tuple[UUID, ...]
    config_versions: tuple[UUID, ...]
    judge_config_version: UUID
    repeat: Annotated[StrictInt, Field(ge=1, le=5)]
    mode: Literal["recorded", "isolated"]
    recording_versions: tuple[UUID, ...]
    environment_version: UUID | None
    policy_revision: str
    operations_revision: str
    inventory: str
    config_fingerprints: dict
    revision: Annotated[StrictInt, Field(ge=1)] = 1
    state: Literal["open", "closed"] = "open"

    @field_validator("requester", "config_fingerprints")
    @classmethod
    def freeze(cls, value):
        return deep_freeze_json(value)


class BudgetBindingSelection(ImmutableModel):
    namespace_id: UUID
    run_id: UUID
    source_entity_id: str = Field(min_length=1, max_length=255)
    case_id: UUID
    config_version_id: UUID
    subject_config_version_id: UUID
    repeat: Annotated[StrictInt, Field(ge=1, le=5)]


class BudgetRunBinding(BudgetBindingSelection):
    purpose: Literal["evaluation_subject", "evaluation_judge"]
    source_entity_type: Literal[
        "evaluation_recorded_case", "evaluation_isolated_case", "evaluation_judge"
    ]
    requester: dict
    config_fingerprint: str
    candidate_proof: dict
    policy_digest: str
    policy_revision: str
    operations_revision: str

    @field_validator("requester", "candidate_proof")
    @classmethod
    def freeze(cls, value):
        return deep_freeze_json(value)
