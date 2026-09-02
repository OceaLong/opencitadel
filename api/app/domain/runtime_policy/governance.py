"""Canonical immutable governance policy for every retained context."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.kernel.domain.types import EffectSafety

REGISTERED_EFFECT_TYPES = frozenset(
    {
        "model.call",
        "knowledge.retrieve",
        "tool.call",
        "file.operation",
        "knowledge.build",
    }
)


class QuotaLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    monthly_model_tokens: int | None = Field(default=None, ge=0)
    daily_new_runs: int | None = Field(default=None, ge=0)
    concurrent_runs: int | None = Field(default=None, ge=0)
    storage_bytes: int | None = Field(default=None, ge=0)


class GovernancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    effect_timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    effect_max_attempts: int = Field(default=3, ge=1, le=20)
    approval_ttl_seconds: int = Field(default=86_400, ge=1, le=2_592_000)
    worker_concurrency: int = Field(default=16, ge=1, le=1_024)
    retention_days: int = Field(default=30, ge=1, le=3_650)
    snapshot_interval: int = Field(default=50, ge=1, le=10_000)
    tool_allowlist: tuple[str, ...] = ()
    tool_denylist: tuple[str, ...] = ()
    safety_overrides: dict[str, EffectSafety] = Field(default_factory=dict)
    user_quota_defaults: QuotaLimits = Field(default_factory=QuotaLimits)
    team_quota_defaults: QuotaLimits = Field(default_factory=QuotaLimits)

    @model_validator(mode="after")
    def _validate_closed_world(self) -> Self:
        unknown = set(self.safety_overrides) - REGISTERED_EFFECT_TYPES
        if unknown:
            raise ValueError(
                "safety override must name a registered Effect type: " + ", ".join(sorted(unknown))
            )
        overlap = set(self.tool_allowlist) & set(self.tool_denylist)
        if overlap:
            raise ValueError("tool allowlist and denylist overlap")
        return self

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class GovernancePolicyRevision(BaseModel):
    model_config = ConfigDict(frozen=True)

    revision_id: UUID
    policy: GovernancePolicy
    actor_user_id: str
    note: str
    created_at: datetime
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        revision_id: UUID,
        policy: GovernancePolicy,
        actor_user_id: str,
        note: str,
        created_at: datetime,
    ) -> Self:
        return cls(
            revision_id=revision_id,
            policy=policy,
            actor_user_id=actor_user_id,
            note=note,
            created_at=created_at,
            digest=policy.digest,
        )
