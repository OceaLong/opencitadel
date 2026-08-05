"""Stable Actuator request/response contracts.

Structure and evidence_for()'s sha256+expiry pattern mirror
ops-collector/src/opencitadel_ops_collector/contracts.py:92-106, with the
evidence ref prefix changed to actuator://evidence/ and the envelope
extended for write-action semantics (action, action_outcome, before/after
observation snapshots) instead of the read-only status/duration_ms pair.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActuatorErrorCode(str, Enum):
    NAMESPACE_DENIED = "NAMESPACE_DENIED"
    TARGET_DENIED = "TARGET_DENIED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    KIND_MISMATCH = "KIND_MISMATCH"
    REPLICAS_OUT_OF_BOUNDS = "REPLICAS_OUT_OF_BOUNDS"
    IDEMPOTENCY_KEY_MISSING = "IDEMPOTENCY_KEY_MISSING"
    NO_ROLLBACK_REVISION = "NO_ROLLBACK_REVISION"
    K8S_ERROR = "K8S_ERROR"
    TIMEOUT = "TIMEOUT"
    OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
    INTERNAL = "INTERNAL"


class EvidenceItem(BaseModel):
    type: Literal["summary", "before_after_snapshot", "actuator_result"]
    ref: str
    sha256: str
    target_ref: str
    expires_at: datetime


class ActuatorEnvelope(BaseModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_ref: str
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: str
    action_outcome: Literal["applied", "skipped_idempotent", "failed"]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: ActuatorErrorCode | None = None
    error_message: str | None = Field(default=None, max_length=2000)


class RestartRequest(BaseModel):
    namespace: str = Field(min_length=1, max_length=253)
    workload: str = Field(min_length=1, max_length=253)
    kind: Literal["deployment", "statefulset"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class ScaleRequest(BaseModel):
    namespace: str = Field(min_length=1, max_length=253)
    workload: str = Field(min_length=1, max_length=253)
    kind: Literal["deployment", "statefulset"]
    replicas: int = Field(ge=0, le=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class RollbackRequest(BaseModel):
    namespace: str = Field(min_length=1, max_length=253)
    workload: str = Field(min_length=1, max_length=253)
    kind: Literal["deployment", "statefulset"]
    idempotency_key: str = Field(min_length=1, max_length=200)


def evidence_for(target_ref: str, data: dict[str, Any], types: list[str]) -> list[EvidenceItem]:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    evidence_id = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    return [
        EvidenceItem(
            type=item,  # type: ignore[arg-type]
            ref=f"actuator://evidence/{evidence_id}/{item}",
            sha256=digest,
            target_ref=target_ref,
            expires_at=expires,
        )
        for item in types
    ]
