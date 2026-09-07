"""Stable execution-kernel rejection and delivery errors."""

from enum import StrEnum

from app.domain.errors import TooManyRequestsError


class RejectionCode(StrEnum):
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"
    EXPECTED_VERSION_CONFLICT = "EXPECTED_VERSION_CONFLICT"
    INVALID_COMMAND_SCHEMA = "INVALID_COMMAND_SCHEMA"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"


class AdmissionLimitExceededError(TooManyRequestsError):
    """Hard per-owner root-Run capacity is exhausted (pending intent included)."""

    def __init__(self, *, limit: int, active: int) -> None:
        super().__init__(
            "Active run capacity is full; wait for a running task to finish and retry",
            error_key="errors.quota.maxConcurrentTasks",
            error_params={"scope": "workspace", "limit": str(limit), "active": str(active)},
        )
        self.limit = limit
        self.active = active
        self.data = {"reason": "ADMISSION_LIMIT_EXCEEDED", "limit": limit, "active": active}


class CommandInProgressError(RuntimeError):
    pass


__all__ = ["AdmissionLimitExceededError", "CommandInProgressError", "RejectionCode"]
