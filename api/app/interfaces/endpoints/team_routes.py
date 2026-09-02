"""Team membership, invitation, and disposition routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.contexts.identity.runtime import IdentityRuntime
from app.domain.models.scope import Principal
from app.interfaces.auth_dependencies import get_current_principal
from app.interfaces.service_dependencies import get_identity_runtime
from app.kernel.interfaces.schemas import ApiModel

router = APIRouter(prefix="/teams", tags=["teams"])
invitation_router = APIRouter(prefix="/invitations", tags=["invitations"])


class TeamBody(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2_000)


class InviteBody(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="member", pattern="^(admin|member)$")


class AcceptBody(ApiModel):
    token: str = Field(min_length=20, max_length=512)


class DispositionBody(ApiModel):
    plan_hash: str = Field(min_length=64, max_length=64)
    confirmation: str = Field(min_length=1, max_length=500)


@router.get("")
async def list_teams(
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return {"data": await runtime.queries.list_teams(principal.user_id)}


@router.post("")
async def create_team(
    body: TeamBody,
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return {
        "data": await runtime.commands.create_team(
            name=body.name,
            description=body.description,
            actor_user_id=principal.user_id,
        )
    }


@router.get("/{team_id}")
async def get_team(
    team_id: str,
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return {
        "data": await runtime.queries.get_team(
            team_id,
            principal.user_id,
            is_admin=principal.is_admin,
        )
    }


@router.post("/{team_id}/invitations")
async def create_invitation(
    team_id: str,
    body: InviteBody,
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return {
        "data": await runtime.commands.invite(
            team_id,
            email=body.email,
            role=body.role,
            actor_user_id=principal.user_id,
        )
    }


@invitation_router.post("/accept")
async def accept_invitation(
    body: AcceptBody,
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return {
        "data": await runtime.commands.accept_invitation(
            body.token,
            actor_user_id=principal.user_id,
        )
    }


@router.get("/{team_id}/disposition")
async def preview_team_disposition(
    team_id: str,
    action: str = Query(pattern="^(archive|restore|purge)$"),
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return {
        "data": await runtime.queries.team_disposition(
            team_id,
            action=action,
            actor_user_id=principal.user_id,
            is_admin=principal.is_admin,
        )
    }


async def _apply(
    team_id: str,
    action: str,
    body: DispositionBody,
    principal: Principal,
    runtime: IdentityRuntime,
):
    return {
        "data": await runtime.commands.apply_team_disposition(
            team_id,
            action=action,
            plan_hash=body.plan_hash,
            confirmation=body.confirmation,
            actor_user_id=principal.user_id,
            is_admin=principal.is_admin,
        )
    }


@router.post("/{team_id}/commands/archive")
async def archive_team(
    team_id: str,
    body: DispositionBody,
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return await _apply(team_id, "archive", body, principal, runtime)


@router.post("/{team_id}/commands/restore")
async def restore_team(
    team_id: str,
    body: DispositionBody,
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return await _apply(team_id, "restore", body, principal, runtime)


@router.post("/{team_id}/commands/purge")
async def purge_team(
    team_id: str,
    body: DispositionBody,
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return await _apply(team_id, "purge", body, principal, runtime)
