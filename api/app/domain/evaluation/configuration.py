"""Immutable evaluation selectors, budgets and private resolved configuration facts."""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.json_values import deep_freeze_json
from app.domain.models.resource_pin import ResourceIdentity

PositiveInt = Annotated[StrictInt, Field(gt=0)]


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def validate_matrix(case_count: int, config_count: int, repeat: int) -> int:
    if any(type(v) is not int for v in (case_count, config_count, repeat)) or not (
        1 <= case_count <= 1000 and 1 <= config_count <= 5 and 1 <= repeat <= 5
    ):
        raise ValueError("invalid matrix dimensions")
    total = case_count * config_count * repeat
    if total > 5000:
        raise ValueError("matrix exceeds 5000")
    return total


class DeploymentLimits(ImmutableModel):
    case_timeout_seconds: PositiveInt = 1800
    batch_timeout_seconds: PositiveInt = 86400
    subject_concurrency: PositiveInt = 5
    judge_concurrency: PositiveInt = 2
    environment_concurrency: PositiveInt = 2


class SuiteSettings(DeploymentLimits):
    token_budget: PositiveInt
    money_budget: Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)] | None = None
    repeat: Annotated[StrictInt, Field(ge=1, le=5)] = 1
    seed: StrictInt = 0
    max_results: Annotated[StrictInt, Field(ge=1, le=5000)] = 5000

    def validate_limits(self, limits: DeploymentLimits) -> None:
        for key in DeploymentLimits.model_fields:
            if getattr(self, key) > getattr(limits, key):
                raise ValueError("deployment_limit_exceeded:" + key)


class ExternalContractReference(ImmutableModel):
    kind: Literal["recording", "environment"]
    version_id: UUID


class ConfigSelection(ImmutableModel):
    model_id: str = Field(min_length=1, max_length=255)
    purpose: Literal["evaluation_subject", "evaluation_judge"] = "evaluation_subject"
    mode: Literal["agent", "ask"] = "agent"
    skill_id: str | None = Field(default=None, max_length=255)
    prompt: str = Field(default="", max_length=32000)
    temperature: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False, strict=True)] | None = None
    max_output_tokens: PositiveInt | None = None
    seed: StrictInt | None = None
    tool_names: tuple[str, ...] = ()
    external_contract_ref: ExternalContractReference | None = None
    resources: tuple[ResourceIdentity, ...] = ()
    knowledge_policy: Literal["fixed_only"] = "fixed_only"

    @model_validator(mode="after")
    def controls(self):
        if self.purpose == "evaluation_judge" and (
            self.tool_names or self.skill_id or self.resources or self.external_contract_ref
        ):
            raise ValueError("judge_tool_free")
        if self.purpose == "evaluation_judge" and self.mode != "ask":
            raise ValueError("judge_requires_ask")
        if len(self.tool_names) > 100 or len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("invalid_tool_names")
        return self


class ConfigVersion(ImmutableModel):
    id: UUID
    entity_id: UUID
    revision: PositiveInt
    name: str
    selection: ConfigSelection
    fingerprint: str
    version_unpinned: bool = True
    unpinned_reasons: tuple[str, ...] = ("provider_revision_unavailable",)
    # Infrastructure/application only: never use this model as a public response DTO.
    snapshot: dict

    @field_validator("snapshot")
    @classmethod
    def freeze(cls, value):
        return deep_freeze_json(value)


class SuiteDefinition(ImmutableModel):
    dataset_version: UUID
    config_versions: tuple[UUID, ...] = Field(min_length=1, max_length=5)
    rubric_version: UUID
    mode: Literal["recorded", "isolated"]
    recording_versions: tuple[UUID, ...] = ()
    environment_version: UUID | None = None
    settings: SuiteSettings

    @model_validator(mode="after")
    def bindings(self):
        if len(set(self.config_versions)) != len(self.config_versions):
            raise ValueError("duplicate_config_version")
        if self.mode == "isolated" and self.environment_version is None:
            raise ValueError("environment_required")
        if self.mode == "recorded" and self.environment_version is not None:
            raise ValueError("recorded_environment_forbidden")
        return self


class DatasetProof(ImmutableModel):
    revision: PositiveInt
    membership_digest: str
    resources: tuple[ResourceIdentity, ...]
    reference_evidence: tuple[tuple[str, bool, tuple[str, ...]], ...]


def dataset_membership_digest(row) -> str:
    """Only persisted immutable manifest/index metadata; no content body or storage URL."""
    members = row["members"]
    if not members or any(
        m.get("cleaned_at") is not None or not m.get("storage_key") for m in members
    ):
        raise ValueError("dataset_metadata_unavailable")
    return digest(
        [
            {
                "id": str(m["id"]),
                "revision": m["revision"],
                "case_key": m["case_key"],
                "object_id": str(m["object_id"]),
                "object_index": m["object_index"],
                "digest": m["digest"],
            }
            for m in members
        ]
    )


class SuiteVersion(SuiteDefinition):
    id: UUID
    entity_id: UUID
    revision: PositiveInt
    name: str
    quantity: PositiveInt
    fingerprint: str
    dataset_proof: DatasetProof
