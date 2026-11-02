"""Immutable definitions and operational publication, owned by caller transactions."""

from datetime import timedelta
from typing import Protocol

from app.domain.execution.definition import DefinitionManifest, DefinitionPin, WorkerCapabilities
from app.domain.execution.family import RunFamily


class ExecutionDefinitions(Protocol):
    async def register(self, manifest: DefinitionManifest) -> None: ...
    async def seed_head(self, manifest: DefinitionManifest) -> None: ...
    async def head(self, family: RunFamily, *, lock: bool = False) -> DefinitionPin: ...
    async def manifest(self, pin: DefinitionPin) -> DefinitionManifest: ...
    async def heartbeat(self, capabilities: WorkerCapabilities, *, ttl: timedelta) -> None: ...
    async def publish_head(
        self,
        pin: DefinitionPin,
        *,
        expected_digest: str,
        actor_user_id: str,
        request_id: str,
        reason: str,
    ) -> DefinitionPin: ...
    async def retirement_references(self, pin: DefinitionPin) -> dict: ...
    async def retire(
        self, pin: DefinitionPin, *, actor_user_id: str, request_id: str, reason: str
    ) -> DefinitionPin: ...
