"""Public evaluation schemas exclude private resolved prompts and credential locators."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.domain.evaluation.configuration import ConfigSelection, SuiteDefinition, SuiteVersion
from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.rubric import RubricDefinition, RubricVersion

Definition = ConfigSelection | RubricDefinition | SuiteDefinition


class CreateConfigurationRequest(ImmutableModel):
    request_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    definition: Definition


class PublishConfigurationRequest(ImmutableModel):
    request_id: str = Field(min_length=1, max_length=255)
    expected_revision: int = Field(ge=1, strict=True)


class UpdateConfigurationRequest(CreateConfigurationRequest):
    expected_revision: int = Field(ge=1, strict=True)


class PublicConfigurationDraft(ImmutableModel):
    id: UUID
    kind: Literal["config", "rubric", "suite"]
    name: str
    revision: int
    definition: Definition


class PublicConfigVersion(ImmutableModel):
    id: UUID
    entity_id: UUID
    revision: int
    name: str
    fingerprint: str
    model_id: str
    purpose: Literal["evaluation_subject", "evaluation_judge"]
    version_unpinned: bool
    unpinned_reasons: tuple[str, ...]
    contract_digest: str
    policy_revision: str


class PublicSuiteVersion(SuiteDefinition):
    id: UUID
    entity_id: UUID
    revision: int
    name: str
    quantity: int
    fingerprint: str


PublicVersion = PublicConfigVersion | RubricVersion | PublicSuiteVersion


def public_version(version):
    from app.domain.evaluation.configuration import ConfigVersion

    if isinstance(version, ConfigVersion):
        return PublicConfigVersion(
            **{
                key: getattr(version, key)
                for key in (
                    "id",
                    "entity_id",
                    "revision",
                    "name",
                    "fingerprint",
                    "version_unpinned",
                    "unpinned_reasons",
                )
            },
            model_id=version.selection.model_id,
            purpose=version.selection.purpose,
            contract_digest=version.snapshot["contract_digest"],
            policy_revision=version.snapshot["policy_revision"],
        )
    if isinstance(version, SuiteVersion):
        return PublicSuiteVersion.model_validate(version.model_dump(exclude={"dataset_proof"}))
    return version


class PreflightRequest(ImmutableModel):
    suite_version: UUID
