"""Narrow application ports for atomic kernel command handling."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import Decision, DecisionFacts
from app.kernel.domain.events import StoredEvent
from app.kernel.domain.types import OwnerScopeRef


class CommandResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"


class CommandResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: UUID
    run_id: UUID
    status: CommandResultStatus
    stream_version: int
    event_ids: tuple[UUID, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


class KernelAuthorization(BaseModel):
    """Already authenticated actor and the owner scopes visible to the request."""

    model_config = ConfigDict(frozen=True)

    actor_user_id: str
    allowed_scopes: tuple[OwnerScopeRef, ...]
    is_admin: bool = False
    is_system: bool = False

    @classmethod
    def for_user(cls, user_id: str, *scopes: OwnerScopeRef):
        return cls(actor_user_id=user_id, allowed_scopes=tuple(scopes))

    @classmethod
    def system(cls, actor: str, scope: OwnerScopeRef):
        return cls(
            actor_user_id=actor,
            allowed_scopes=(scope,),
            is_system=True,
        )

    def allows(self, scope: OwnerScopeRef) -> bool:
        return self.is_admin or self.is_system or scope in self.allowed_scopes


class KernelTransaction(Protocol):
    async def get_command_result(self, command_id: UUID) -> CommandResult | None: ...

    async def reserve_command(self, command: CommandEnvelope) -> CommandResult | None: ...

    async def load_events(self, run_id: UUID) -> tuple[StoredEvent, ...]: ...

    async def validate_command(self, command: CommandEnvelope) -> None: ...

    async def append_decision(
        self,
        command: CommandEnvelope,
        decision: Decision,
        facts: DecisionFacts,
    ) -> CommandResult: ...

    async def reject_command(
        self,
        command: CommandEnvelope,
        *,
        code: str,
        message: str,
    ) -> CommandResult: ...


class KernelStore(Protocol):
    def transaction(
        self,
        authorization: KernelAuthorization,
    ) -> AbstractAsyncContextManager[KernelTransaction]: ...
