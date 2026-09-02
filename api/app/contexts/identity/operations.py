"""Team, invitation, administration, audit, and notification operations."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import ConflictError, ForbiddenError, NotFoundError
from app.domain.models.authorization import AuthorizationContext
from app.kernel.infrastructure.postgres.models import KernelNotificationViewORM
from app.kernel.infrastructure.postgres.session_auth import bind_context

from .models import (
    AuditRecordORM,
    InvitationORM,
    TeamMemberORM,
    TeamORM,
    UserORM,
)
from .services import _append_audit


class PostgresIdentityOperations:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        retention_days: int,
        storage: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._retention_days = retention_days
        self._storage = storage

    async def list_teams(self, user_id: str) -> list[dict[str, object]]:
        async with self._system_session("team-query") as session:
            rows = (
                await session.execute(
                    select(TeamORM, TeamMemberORM.role)
                    .join(TeamMemberORM, TeamMemberORM.team_id == TeamORM.id)
                    .where(TeamMemberORM.user_id == user_id)
                    .order_by(TeamORM.created_at)
                )
            ).all()
        return [self._team_view(row, role) for row, role in rows]

    async def get_team(self, team_id: str, user_id: str, *, is_admin: bool) -> dict[str, object]:
        async with self._system_session("team-query") as session:
            row = await session.get(TeamORM, team_id)
            role = await session.scalar(
                select(TeamMemberORM.role).where(
                    TeamMemberORM.team_id == team_id,
                    TeamMemberORM.user_id == user_id,
                )
            )
        if row is None or (role is None and not is_admin):
            raise NotFoundError("Team not found")
        return self._team_view(row, role)

    async def create_team(
        self,
        *,
        name: str,
        description: str,
        actor_user_id: str,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        row = TeamORM(
            id=str(uuid4()),
            name=name,
            description=description,
            created_by_user_id=actor_user_id,
            archived_at=None,
            purge_after=None,
            created_at=now,
            updated_at=now,
        )
        async with self._system_session("team-command") as session:
            if await session.get(UserORM, actor_user_id) is None:
                raise NotFoundError("User not found")
            session.add(row)
            await session.flush()
            session.add(
                TeamMemberORM(
                    team_id=row.id,
                    user_id=actor_user_id,
                    role="owner",
                    joined_at=now,
                )
            )
            await _append_audit(
                session,
                shard_key=f"team:{row.id}",
                actor_user_id=actor_user_id,
                action="team.created",
                resource_type="team",
                resource_id=row.id,
                metadata={"name": name},
                now=now,
            )
        return self._team_view(row, "owner")

    async def invite(
        self,
        team_id: str,
        *,
        email: str,
        role: str,
        actor_user_id: str,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        async with self._system_session("invitation-command") as session:
            await self._require_team_admin(session, team_id, actor_user_id)
            invitation = InvitationORM(
                id=uuid4(),
                email=email.strip().lower(),
                team_id=team_id,
                role=role,
                token_digest=hashlib.sha256(token.encode()).hexdigest(),
                invited_by_user_id=actor_user_id,
                expires_at=now + timedelta(days=7),
                accepted_at=None,
                created_at=now,
            )
            session.add(invitation)
        return {
            "id": str(invitation.id),
            "teamId": team_id,
            "email": invitation.email,
            "role": role,
            "token": token,
            "expiresAt": invitation.expires_at.isoformat(),
        }

    async def accept_invitation(self, token: str, *, actor_user_id: str) -> dict[str, object]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC)
        async with self._system_session("invitation-accept") as session:
            invitation = await session.scalar(
                select(InvitationORM).where(InvitationORM.token_digest == digest).with_for_update()
            )
            user = await session.get(UserORM, actor_user_id)
            if (
                invitation is None
                or invitation.accepted_at is not None
                or invitation.expires_at <= now
                or user is None
                or invitation.email != user.email
            ):
                raise ConflictError("Invitation is invalid or expired")
            existing = await session.get(
                TeamMemberORM,
                {"team_id": invitation.team_id, "user_id": actor_user_id},
            )
            if existing is None:
                session.add(
                    TeamMemberORM(
                        team_id=invitation.team_id,
                        user_id=actor_user_id,
                        role=invitation.role,
                        joined_at=now,
                    )
                )
            invitation.accepted_at = now
        return {"teamId": invitation.team_id, "role": invitation.role}

    async def team_disposition(
        self,
        team_id: str,
        *,
        action: str,
        actor_user_id: str,
        is_admin: bool,
    ) -> dict[str, object]:
        team = await self.get_team(team_id, actor_user_id, is_admin=is_admin)
        bound = {
            "action": action,
            "resourceId": team_id,
            "updatedAt": team["updatedAt"],
            "confirmation": f"{action.upper()} TEAM {team_id}",
            "recoverable": action == "archive",
        }
        return {
            **bound,
            "planHash": hashlib.sha256(
                json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "purgeAfter": (datetime.now(UTC) + timedelta(days=self._retention_days)).isoformat()
            if action == "archive"
            else datetime.now(UTC).isoformat(),
        }

    async def apply_team_disposition(
        self,
        team_id: str,
        *,
        action: str,
        plan_hash: str,
        confirmation: str,
        actor_user_id: str,
        is_admin: bool,
    ) -> dict[str, object]:
        plan = await self.team_disposition(
            team_id,
            action=action,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
        )
        if not hmac.compare_digest(plan_hash, str(plan["planHash"])) or not hmac.compare_digest(
            confirmation, str(plan["confirmation"])
        ):
            raise ConflictError("stale team disposition plan")
        now = datetime.now(UTC)
        storage_refs: list[str] = []
        async with self._system_session("team-disposition") as session:
            if not is_admin:
                await self._require_team_admin(session, team_id, actor_user_id)
            team = await session.get(TeamORM, team_id)
            if team is None:
                raise NotFoundError("Team not found")
            if action == "archive":
                team.archived_at = now
                team.purge_after = now + timedelta(days=self._retention_days)
                team.updated_at = now
            elif action == "restore":
                team.archived_at = None
                team.purge_after = None
                team.updated_at = now
            elif action == "purge":
                storage_refs = await self._purge_team_content(session, team_id)
                await session.delete(team)
            else:
                raise ValueError("unsupported team disposition")
            await _append_audit(
                session,
                shard_key=f"team:{team_id}",
                actor_user_id=actor_user_id,
                action=f"team.{action}",
                resource_type="team",
                resource_id=team_id,
                metadata={"planHash": plan_hash},
                now=now,
            )
        if self._storage is not None:
            for storage_ref in storage_refs:
                await self._storage.delete_bytes(storage_ref)
        return {"action": action, "resourceId": team_id}

    async def list_users(self) -> list[dict[str, object]]:
        from .auth import user_view

        async with self._system_session("admin-query") as session:
            rows = (await session.scalars(select(UserORM).order_by(UserORM.created_at))).all()
        return [user_view(row) for row in rows]

    async def update_user(
        self,
        user_id: str,
        *,
        enabled: bool | None,
        global_role: str | None,
        actor_user_id: str,
    ) -> dict[str, object]:
        from .auth import user_view

        if user_id == actor_user_id and enabled is False:
            raise ConflictError("Administrator cannot disable the current account")
        now = datetime.now(UTC)
        async with self._system_session("admin-user-command") as session:
            row = await session.get(UserORM, user_id)
            if row is None:
                raise NotFoundError("User not found")
            changed = False
            if enabled is not None and enabled != row.enabled:
                row.enabled = enabled
                changed = True
            if global_role is not None and global_role != row.global_role:
                row.global_role = global_role
                changed = True
            if changed:
                row.token_version += 1
                row.updated_at = now
                await _append_audit(
                    session,
                    shard_key=f"user:{user_id}",
                    actor_user_id=actor_user_id,
                    action="user.updated",
                    resource_type="user",
                    resource_id=user_id,
                    metadata={"enabled": row.enabled, "globalRole": row.global_role},
                    now=now,
                )
            return user_view(row)

    async def list_admin_teams(self) -> list[dict[str, object]]:
        async with self._system_session("admin-query") as session:
            rows = (await session.scalars(select(TeamORM).order_by(TeamORM.created_at))).all()
        return [self._team_view(row, None) for row in rows]

    async def list_audit(self, *, limit: int) -> list[dict[str, object]]:
        async with self._system_session("audit-query") as session:
            rows = (
                await session.scalars(
                    select(AuditRecordORM).order_by(AuditRecordORM.created_at.desc()).limit(limit)
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "actorUserId": row.actor_user_id,
                "action": row.action,
                "resourceType": row.resource_type,
                "resourceId": row.resource_id,
                "metadata": row.metadata_json,
                "previousHash": row.previous_hash,
                "hash": row.hash,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]

    async def list_notifications(self, user_id: str) -> list[dict[str, object]]:
        async with self._system_session("notification-query") as session:
            rows = (
                await session.scalars(
                    select(KernelNotificationViewORM)
                    .where(KernelNotificationViewORM.user_id == user_id)
                    .order_by(KernelNotificationViewORM.created_at.desc())
                    .limit(100)
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "kind": row.kind,
                "payload": row.payload,
                "read": row.read,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]

    async def mark_notification_read(self, notification_id: UUID, user_id: str) -> bool:
        async with self._system_session("notification-command") as session:
            result = await session.execute(
                update(KernelNotificationViewORM)
                .where(
                    KernelNotificationViewORM.id == notification_id,
                    KernelNotificationViewORM.user_id == user_id,
                )
                .values(read=True)
            )
            return result.rowcount == 1

    @staticmethod
    async def _require_team_admin(
        session: AsyncSession,
        team_id: str,
        user_id: str,
    ) -> None:
        role = await session.scalar(
            select(TeamMemberORM.role).where(
                TeamMemberORM.team_id == team_id,
                TeamMemberORM.user_id == user_id,
            )
        )
        if role not in {"owner", "admin"}:
            raise ForbiddenError("Team administrator permission required")

    @staticmethod
    async def _purge_team_content(session: AsyncSession, team_id: str) -> list[str]:
        storage_refs = list(
            await session.scalars(
                text(
                    "SELECT storage_ref FROM files WHERE team_id = :team_id "
                    "UNION SELECT storage_ref FROM artifacts WHERE team_id = :team_id "
                    "UNION SELECT d.storage_ref FROM knowledge_documents d "
                    "JOIN knowledge_bases b ON b.id = d.knowledge_base_id "
                    "WHERE b.team_id = :team_id"
                ),
                {"team_id": team_id},
            )
        )
        await bind_context(session, AuthorizationContext.system("kernel-purge"))
        for table_name in (
            "kernel_commands",
            "kernel_events",
            "kernel_effects",
            "kernel_timers",
            "kernel_run_views",
            "kernel_message_views",
            "kernel_effect_views",
            "kernel_approval_views",
            "kernel_public_events",
            "kernel_resource_build_views",
            "inference_usage",
            "inference_bindings",
            "inference_models",
            "inference_endpoints",
            "mcp_servers",
            "artifacts",
            "files",
            "knowledge_bases",
        ):
            await session.execute(
                text(f"DELETE FROM {table_name} WHERE team_id = :team_id"),
                {"team_id": team_id},
            )
        await session.execute(
            text("DELETE FROM kernel_runs WHERE team_id = :team_id"),
            {"team_id": team_id},
        )
        return [str(value) for value in storage_refs]

    @staticmethod
    def _team_view(row: TeamORM, role: str | None) -> dict[str, object]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "role": role,
            "archivedAt": row.archived_at.isoformat() if row.archived_at else None,
            "purgeAfter": row.purge_after.isoformat() if row.purge_after else None,
            "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(),
        }

    def _system_session(self, actor: str):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def open_session():
            async with self._session_factory() as session, session.begin():
                await bind_context(session, AuthorizationContext.system(actor))
                yield session

        return open_session()


__all__ = ["PostgresIdentityOperations"]
