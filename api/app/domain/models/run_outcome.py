#!/usr/bin/env python
# -*- coding: utf-8 -*-
from enum import Enum
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, model_serializer


class RunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING = "waiting"


class RunError(BaseModel):
    message: str
    code: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _serialize_without_empty_details(self, handler):
        payload = handler(self)
        if not payload.get("details"):
            payload.pop("details", None)
        return payload


class RunOutcome(BaseModel):
    status: RunStatus
    error: Optional[RunError] = None
    usage: Dict[str, int | float] = Field(default_factory=dict)

    @classmethod
    def succeeded(cls, *, usage: Optional[Dict[str, int | float]] = None) -> "RunOutcome":
        return cls(status=RunStatus.SUCCEEDED, usage=usage or {})

    @classmethod
    def failed(
            cls,
            message: str,
            *,
            code: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None,
            usage: Optional[Dict[str, int | float]] = None,
    ) -> "RunOutcome":
        return cls(
            status=RunStatus.FAILED,
            error=RunError(
                message=message,
                code=code,
                details=details or {},
            ),
            usage=usage or {},
        )

    @classmethod
    def cancelled(
            cls,
            message: str = "Run cancelled",
            *,
            code: str = "RUN_CANCELLED",
            details: Optional[Dict[str, Any]] = None,
            usage: Optional[Dict[str, int | float]] = None,
    ) -> "RunOutcome":
        return cls(
            status=RunStatus.CANCELLED,
            error=RunError(
                message=message,
                code=code,
                details=details or {},
            ),
            usage=usage or {},
        )

    @classmethod
    def waiting(
            cls,
            *,
            usage: Optional[Dict[str, int | float]] = None,
    ) -> "RunOutcome":
        return cls(status=RunStatus.WAITING, usage=usage or {})


class RunReconciliationEnvelope(BaseModel):
    """Durable input-stream handoff for an outcome not yet in task metadata."""

    type: Literal["run_reconciliation"] = "run_reconciliation"
    run_epoch_id: str
    outcome: RunOutcome
