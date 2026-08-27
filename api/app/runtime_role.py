"""Immutable process-role values passed through explicit composition roots."""

from __future__ import annotations

from enum import StrEnum


class ProcessRole(StrEnum):
    API = "api"
    EXECUTION_KERNEL = "execution-kernel"
    MIGRATE = "migrate"
    SEED = "seed"
    SANDBOX_BROKER = "sandbox-broker"


__all__ = ["ProcessRole"]
