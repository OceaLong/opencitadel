"""Identity context composition value."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IdentityRuntime:
    commands: Any
    queries: Any
    transactions: Any
    auth: Any = None
    quotas: Any = None
    governance: Any = None
    cookies: Any = None
    csrf: Any = None
    oauth: Any = None
    application_urls: Any = None
