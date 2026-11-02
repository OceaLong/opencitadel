"""Explicit renewal identities; completed work and old approvals stay immutable."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RefreshToolIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    refresh_id: UUID
    activity_id: UUID
    expected_subject_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_user_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class ToolIntentRefreshRecord(RefreshToolIntent):
    root_activity_id: UUID
    prepare_activity_id: UUID
    replacement_activity_id: UUID
    generation: int = Field(ge=0)
