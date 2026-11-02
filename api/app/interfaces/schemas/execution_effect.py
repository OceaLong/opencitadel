"""Public evidence-bound requests; no endpoint accepts an execution/usage status."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ref: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EffectAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=2000)
    action: str = Field(min_length=1, max_length=255)
    target: str = Field(pattern=r"^[0-9a-f]{64}$")
    occurred_at: AwareDatetime
    method: str = Field(min_length=1, max_length=4000)
    responsible_actor: str = Field(min_length=1, max_length=255)


class EffectQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=0)


class EffectResolutionRequest(EffectQueryRequest):
    decision: Literal["applied", "not_applied"]
    evidence_refs: list[EvidenceReference] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=4000)
    attestation: EffectAttestation
    result_evidence_ref: EvidenceReference | None = None


class EffectAbandonRequest(EffectQueryRequest):
    reason: str = Field(min_length=1, max_length=4000)


class EffectRequestResponse(BaseModel):
    request_id: UUID
    run_id: UUID
    operation_id: UUID
    status: str
    expected_revision: int
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class EffectOperationResponse(BaseModel):
    operation_id: UUID
    run_id: UUID
    invocation_id: UUID
    activity_id: UUID
    revision: int
    state: str
    capability: str | dict[str, Any]
    wait_reason: str | None = None
    contract_digest: str
    arguments_digest: str
    target_digest: str
    provider_identity: str | dict[str, Any]
    allowed_actions: list[str] = Field(default_factory=list)
    verification_binding: dict[str, Any] = Field(default_factory=dict)
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    query_history: list[dict[str, Any]] = Field(default_factory=list)
    accounting_state: str = "independent"
    contract_drift: dict[str, Any] | None = None
    last_confirmed_fact: dict[str, Any] | None = None
