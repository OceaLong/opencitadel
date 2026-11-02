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
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActuatorErrorCode(StrEnum):
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
    OPERATION_CONFLICT = "OPERATION_CONFLICT"
    OPERATION_NOT_FOUND = "OPERATION_NOT_FOUND"
    OPERATION_UNKNOWN = "OPERATION_UNKNOWN"
    OPERATION_PENDING = "OPERATION_PENDING"
    OPERATION_JOURNAL_UNAVAILABLE = "OPERATION_JOURNAL_UNAVAILABLE"
    OPERATION_JOURNAL_TOO_LARGE = "OPERATION_JOURNAL_TOO_LARGE"
    OPERATION_JOURNAL_SENSITIVE = "OPERATION_JOURNAL_SENSITIVE"
    PAYLOAD_DIGEST_MISMATCH = "PAYLOAD_DIGEST_MISMATCH"
    TARGET_UID_MISMATCH = "TARGET_UID_MISMATCH"


class EvidenceItem(BaseModel):
    type: Literal["summary", "before_after_snapshot", "actuator_result"]
    ref: str
    sha256: str
    target_ref: str
    expires_at: datetime


class ActuatorEnvelope(BaseModel):
    schema_version: Literal[2] = 2
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_ref: str
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: str
    action_outcome: Literal["applied", "skipped_idempotent", "failed", "pending", "unknown"]
    operation_id: str | None = None
    state: Literal["pending", "applied", "not_applied", "unknown"] | None = None
    receipt_revision: int = 0
    payload_digest: str | None = None
    key_digest: str | None = None
    target: dict[str, str] = Field(default_factory=dict)
    receipt: dict[str, Any] | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: ActuatorErrorCode | None = None
    error_message: str | None = Field(default=None, max_length=2000)


class OperationRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9:_-]+$")
    payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_uid: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9:_-]+$")


class GetOperationRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9:_-]+$")


class GetTargetRequest(BaseModel):
    namespace: str = Field(min_length=1, max_length=253)
    workload: str = Field(min_length=1, max_length=253)
    kind: Literal["deployment", "statefulset"]


class TargetIdentity(BaseModel):
    schema_version: Literal[2] = 2
    target_ref: str
    namespace: str
    workload: str
    kind: Literal["deployment", "statefulset"]
    uid: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9:_-]+$")


class RestartRequest(OperationRequest):
    namespace: str = Field(min_length=1, max_length=253)
    workload: str = Field(min_length=1, max_length=253)
    kind: Literal["deployment", "statefulset"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class ScaleRequest(RestartRequest):
    namespace: str = Field(min_length=1, max_length=253)
    workload: str = Field(min_length=1, max_length=253)
    kind: Literal["deployment", "statefulset"]
    replicas: int = Field(ge=0, le=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class RollbackRequest(RestartRequest):
    namespace: str = Field(min_length=1, max_length=253)
    workload: str = Field(min_length=1, max_length=253)
    kind: Literal["deployment", "statefulset"]
    idempotency_key: str = Field(min_length=1, max_length=200)


def operation_payload_digest(
    action: str,
    target_ref: str,
    namespace: str,
    workload: str,
    kind: str,
    target_uid: str,
    parameters: dict[str, Any] | None = None,
) -> str:
    """Public canonical v2 input digest; neither delivery identity nor credentials enter it."""
    payload = {
        "action": action,
        "target_ref": target_ref,
        "namespace": namespace,
        "workload": workload,
        "kind": kind,
        "target_uid": target_uid,
        "parameters": parameters or {},
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evidence_for(target_ref: str, data: dict[str, Any], types: list[str]) -> list[EvidenceItem]:
    canonical = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    evidence_id = str(uuid.uuid4())
    expires = datetime.now(UTC) + timedelta(days=7)
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
