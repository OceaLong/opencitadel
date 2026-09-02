"""Canonical governance policy API with optimistic head updates."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field

from app.contexts.identity.runtime import IdentityRuntime
from app.domain.models.scope import Principal
from app.domain.runtime_policy.governance import GovernancePolicy
from app.interfaces.auth_dependencies import require_admin
from app.interfaces.schemas import Response
from app.interfaces.service_dependencies import get_identity_runtime
from app.kernel.interfaces.schemas import ApiModel

router = APIRouter(prefix="/governance-policy", tags=["governance"])


class GovernancePolicyUpdate(ApiModel):
    expected_generation: int = Field(ge=1)
    note: str = Field(default="", max_length=2_000)
    policy: GovernancePolicy


@router.get("")
async def get_policy(
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    _: Principal = Depends(require_admin),
):
    return Response.success(data=await runtime.governance.get_active())


@router.put("")
async def update_policy(
    body: GovernancePolicyUpdate,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    actor: Principal = Depends(require_admin),
):
    value = await runtime.governance.update(
        body.policy.model_dump(mode="json"),
        expected_generation=body.expected_generation,
        actor_user_id=actor.user_id,
        note=body.note,
    )
    return Response.success(data=value)
