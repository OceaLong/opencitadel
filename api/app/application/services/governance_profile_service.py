"""Auditor-facing governance profile backed by formal execution projections."""

from collections.abc import Callable
from typing import Any

from app.application.ports.queries import RunProjectionPort
from app.domain.errors import NotFoundError
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.repositories.uow import IUnitOfWork


class GovernanceProfileService:
    """Read a session and its verified Run/Approval/Activity history."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        run_projection: RunProjectionPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._runs = run_projection

    async def build_profile(
        self,
        session_id: str,
        scope: OwnerScope | None = None,
    ) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id, scope=scope)
            patrol_run = (
                await uow.patrol.get_run_by_session_id(session_id) if session is not None else None
            )
        if session is None:
            raise NotFoundError("session not found")
        source_entity_type = "patrol_run" if patrol_run is not None else "session"
        source_entity_id = patrol_run.id if patrol_run is not None else session_id
        execution = await self._runs.source_governance(
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            owner_scope=scope,
        )
        return {
            "session": self._session_row(session),
            **execution,
        }

    @staticmethod
    def _session_row(session: Session) -> dict[str, Any]:
        return {
            "id": session.id,
            "title": session.title,
            "owner_user_id": session.owner_user_id,
            "team_id": session.team_id,
            "status": session.status.value,
            "operator_scope": session.operator_scope,
            "operator_domains": list(session.operator_domains),
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }


__all__ = ["GovernanceProfileService"]
