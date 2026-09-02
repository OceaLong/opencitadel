"""Minimal administration surface retained by the greenfield product."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.contexts.identity.runtime import IdentityRuntime
from app.domain.models.scope import Principal
from app.interfaces.auth_dependencies import require_admin
from app.interfaces.schemas import Response
from app.interfaces.service_dependencies import get_identity_runtime
from app.kernel.interfaces.schemas import ApiModel

router = APIRouter(prefix="/admin", tags=["admin"])


class QuotaRequest(ApiModel):
    monthly_model_tokens: int | None = Field(default=None, ge=0)
    daily_new_runs: int | None = Field(default=None, ge=0)
    concurrent_runs: int | None = Field(default=None, ge=0)
    storage_bytes: int | None = Field(default=None, ge=0)


class UserUpdateRequest(ApiModel):
    enabled: bool | None = None
    global_role: Literal["admin", "user", "auditor"] | None = None


@router.get("/users")
async def list_users(
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    _: Principal = Depends(require_admin),
):
    return Response.success(data=await runtime.queries.list_users())


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    actor: Principal = Depends(require_admin),
):
    return Response.success(
        data=await runtime.commands.update_user(
            user_id,
            enabled=body.enabled,
            global_role=body.global_role,
            actor_user_id=actor.user_id,
        )
    )


@router.get("/teams")
async def list_teams(
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    _: Principal = Depends(require_admin),
):
    return Response.success(data=await runtime.queries.list_admin_teams())


@router.get("/audit")
async def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    _: Principal = Depends(require_admin),
):
    return Response.success(data=await runtime.queries.list_audit(limit=limit))


async def _get_quota(
    kind: Literal["user", "team"],
    subject_id: str,
    runtime: IdentityRuntime,
) -> Response[dict[str, object]]:
    return Response.success(data=await runtime.quotas.get(kind, subject_id))


async def _set_quota(
    kind: Literal["user", "team"],
    subject_id: str,
    body: QuotaRequest,
    runtime: IdentityRuntime,
    actor: Principal,
) -> Response[dict[str, object]]:
    value = await runtime.quotas.set(
        kind,
        subject_id,
        body.model_dump(mode="json", by_alias=True),
        actor_user_id=actor.user_id,
    )
    return Response.success(data=value)


@router.get("/quotas/users/{user_id}")
async def get_user_quota(
    user_id: str,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    _: Principal = Depends(require_admin),
):
    return await _get_quota("user", user_id, runtime)


@router.put("/quotas/users/{user_id}")
async def set_user_quota(
    user_id: str,
    body: QuotaRequest,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    actor: Principal = Depends(require_admin),
):
    return await _set_quota("user", user_id, body, runtime, actor)


@router.get("/quotas/teams/{team_id}")
async def get_team_quota(
    team_id: str,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    _: Principal = Depends(require_admin),
):
    return await _get_quota("team", team_id, runtime)


@router.put("/quotas/teams/{team_id}")
async def set_team_quota(
    team_id: str,
    body: QuotaRequest,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
    actor: Principal = Depends(require_admin),
):
    return await _set_quota("team", team_id, body, runtime, actor)
