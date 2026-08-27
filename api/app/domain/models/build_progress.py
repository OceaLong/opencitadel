"""Private progress values emitted inside one resource-build Activity."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class BuildProgressStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"


class BuildProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["step", "message", "error", "done"]
    phase: str | None = None
    status: BuildProgressStatus | None = None
    message: str = ""
    failure_code: str | None = None


def build_step(
    phase: str,
    message: str,
    status: BuildProgressStatus,
) -> BuildProgress:
    return BuildProgress(
        kind="step",
        phase=phase,
        status=status,
        message=message,
    )


def build_message(message: str) -> BuildProgress:
    return BuildProgress(kind="message", message=message)


def build_error(
    *,
    message: str,
    failure_code: str | None = None,
) -> BuildProgress:
    return BuildProgress(
        kind="error",
        message=message,
        failure_code=failure_code,
    )


def build_done() -> BuildProgress:
    return BuildProgress(kind="done")


__all__ = [
    "BuildProgress",
    "BuildProgressStatus",
    "build_done",
    "build_error",
    "build_message",
    "build_step",
]
