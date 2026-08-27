"""Stable execution-kernel rejection and delivery errors."""

from enum import StrEnum


class RejectionCode(StrEnum):
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"
    EXPECTED_VERSION_CONFLICT = "EXPECTED_VERSION_CONFLICT"
    INVALID_COMMAND_SCHEMA = "INVALID_COMMAND_SCHEMA"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"


class CommandInProgressError(RuntimeError):
    pass


__all__ = ["CommandInProgressError", "RejectionCode"]
