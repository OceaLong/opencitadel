"""Pure reducer outputs and explicit non-deterministic facts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .effects import EffectDeclaration, TimerDeclaration
from .events import NewEvent


class DecisionRejected(RuntimeError):
    """A deterministic, safe-to-persist command rejection."""

    code = "command_rejected"


class StaleEffectClaim(DecisionRejected):
    """An Effect result does not own the current durable claim generation."""

    code = "stale_effect_claim"


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    events: tuple[NewEvent, ...] = ()
    effects: tuple[EffectDeclaration, ...] = ()
    timers: tuple[TimerDeclaration, ...] = ()


class DecisionFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    now: datetime
    actor_user_id: str
    request_id: str
    policy_revision_id: UUID
    event_ids: tuple[UUID, ...] = ()
    effect_ids: tuple[UUID, ...] = ()
    timer_ids: tuple[UUID, ...] = ()
    approval_ids: tuple[UUID, ...] = ()
    reviewer_user_ids: tuple[str, ...] = ()
    approval_ttl_seconds: int = 86_400
