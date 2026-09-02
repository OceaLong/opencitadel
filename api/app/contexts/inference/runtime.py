"""Inference context composition value."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InferenceRuntime:
    commands: Any
    queries: Any
    gateway: Any
    transactions: Any
