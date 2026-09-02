"""Knowledge context composition value."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class KnowledgeRuntime:
    commands: Any
    queries: Any
    gateway: Any
    transactions: Any
    dispositions: Any = None
