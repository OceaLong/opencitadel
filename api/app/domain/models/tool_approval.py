#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Persistent approval state for preflighted tool-call batches."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Optional, Sequence
import uuid

from pydantic import BaseModel, Field

from .tool_policy import (
    ApprovalMode,
    CONSERVATIVE_TOOL_POLICY,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


def _conservative_policy() -> ToolExecutionPolicy:
    return CONSERVATIVE_TOOL_POLICY.model_copy(deep=True)


@dataclass(frozen=True)
class ApprovalCallInput:
    tool_call_id: str
    tool_name: str
    normalized_args: dict[str, Any]
    ordinal: int
    policy: ToolExecutionPolicy = field(default_factory=_conservative_policy)


class ToolApprovalCall(BaseModel):
    id: str
    batch_id: str
    tool_call_id: str
    ordinal: int
    tool_name: str
    normalized_args: dict[str, Any] = Field(default_factory=dict)
    args_hash: str
    capability: ToolCapability
    effect: ToolEffect
    idempotency: ToolIdempotency
    approval: ApprovalMode
    concurrency_group: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


class ToolApprovalBatch(BaseModel):
    id: str
    session_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = None
    calls: list[ToolApprovalCall] = Field(default_factory=list)
    # Transient return metadata: true only for the transaction that moved an
    # approved batch to CONSUMED. It is deliberately not serialized/persisted.
    execution_claimed: bool = Field(default=False, exclude=True)

    @classmethod
    def for_calls(
        cls,
        session_id: str,
        calls: Sequence[ApprovalCallInput],
        *,
        expires_at: Optional[datetime] = None,
        ttl: timedelta = timedelta(minutes=15),
        batch_id: Optional[str] = None,
    ) -> "ToolApprovalBatch":
        if expires_at is not None and (
            expires_at.tzinfo is None or expires_at.utcoffset() is None
        ):
            raise ValueError("approval expires_at must be timezone-aware")
        call_ids = [call.tool_call_id for call in calls]
        ordinals = [call.ordinal for call in calls]
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("approval batch tool_call_id values must be unique")
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("approval batch ordinals must be unique")

        now = datetime.now(timezone.utc)
        resolved_batch_id = batch_id or str(uuid.uuid4())
        approval_calls = [
            ToolApprovalCall(
                id=str(uuid.uuid4()),
                batch_id=resolved_batch_id,
                tool_call_id=call.tool_call_id,
                ordinal=call.ordinal,
                tool_name=call.tool_name,
                normalized_args=call.normalized_args,
                args_hash=hash_normalized_args(call.normalized_args),
                capability=call.policy.capability,
                effect=call.policy.effect,
                idempotency=call.policy.idempotency,
                approval=call.policy.approval,
                concurrency_group=call.policy.concurrency_group,
            )
            for call in sorted(calls, key=lambda item: item.ordinal)
        ]
        return cls(
            id=resolved_batch_id,
            session_id=session_id,
            expires_at=expires_at or now + ttl,
            created_at=now,
            calls=approval_calls,
        )


def hash_normalized_args(normalized_args: dict[str, Any]) -> str:
    encoded = json.dumps(
        normalized_args,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
