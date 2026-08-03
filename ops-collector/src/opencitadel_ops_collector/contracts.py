"""Stable Collector request/response contracts."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class CollectorErrorCode(str, Enum):
    AUTH_FAILED = "AUTH_FAILED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    NAMESPACE_DENIED = "NAMESPACE_DENIED"
    PROBE_DENIED = "PROBE_DENIED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
    REDACTION_FAILED = "REDACTION_FAILED"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class EvidenceItem(BaseModel):
    type: Literal[
        "summary", "resource_refs", "raw_probe_response", "log_excerpt",
        "metric_sample", "tls_chain", "backup_metadata", "collector_result"
    ]
    ref: str
    sha256: str
    target_ref: str
    expires_at: datetime


class CollectorEnvelope(BaseModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_ref: str
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: int = Field(ge=0)
    status: Literal["ok", "error", "unavailable", "denied"]
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: CollectorErrorCode | None = None
    error_message: str | None = Field(default=None, max_length=2000)


class NamespaceRequest(BaseModel):
    namespace: str = Field(min_length=1, max_length=253)


class WorkloadSummaryRequest(NamespaceRequest):
    workload_ids: list[str] = Field(default_factory=list, max_length=100)
    window_seconds: int = Field(default=3600, ge=60, le=3600)
    pvc_query_id: str | None = Field(default=None, min_length=1, max_length=128)


class RecentEventsRequest(NamespaceRequest):
    since_seconds: int = Field(default=3600, ge=60, le=86400)
    limit: int = Field(default=100, ge=1, le=200)


class PodLogsRequest(NamespaceRequest):
    pod: str = Field(min_length=1, max_length=253)
    container: str | None = Field(default=None, max_length=253)
    tail_lines: int = Field(default=100, ge=1, le=200)
    since_seconds: int = Field(default=600, ge=1, le=3600)


class RegisteredQueryRequest(BaseModel):
    query_id: str = Field(min_length=1, max_length=128)
    window_seconds: int = Field(default=300, ge=60, le=3600)


class RegisteredProbeRequest(BaseModel):
    probe_id: str = Field(min_length=1, max_length=128)


class RegisteredBackupRequest(BaseModel):
    backup_id: str = Field(min_length=1, max_length=128)


class RegisteredDependencyRequest(BaseModel):
    dependency_id: str = Field(min_length=1, max_length=128)


def evidence_for(target_ref: str, data: dict[str, Any], types: list[str]) -> list[EvidenceItem]:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    evidence_id = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    return [
        EvidenceItem(
            type=item,  # type: ignore[arg-type]
            ref=f"collector://evidence/{evidence_id}/{item}",
            sha256=digest,
            target_ref=target_ref,
            expires_at=expires,
        )
        for item in types
    ]
