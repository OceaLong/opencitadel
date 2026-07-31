#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Authoritative, owner-scoped resource-build event lifecycle."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from app.application.errors.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
    build_phase_regresses,
)
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

MAX_EVENT_PAGE_SIZE = 500
_TERMINAL_STATES = {
    BuildState.SUCCEEDED,
    BuildState.DEGRADED,
    BuildState.FAILED,
    BuildState.CANCELLED,
}
_ALLOWED_TRANSITIONS = {
    BuildState.QUEUED: {
        BuildState.QUEUED,
        BuildState.RUNNING,
        BuildState.FAILED,
        BuildState.CANCELLED,
    },
    BuildState.RUNNING: {
        BuildState.RUNNING,
        BuildState.SUCCEEDED,
        BuildState.DEGRADED,
        BuildState.FAILED,
        BuildState.CANCELLED,
    },
}


class ResourceBuildEventNotifier(Protocol):
    async def publish(self, build_id: str, seq: int) -> None:
        """Publish only the committed build identity and sequence hint."""
        ...


class ResourceBuildService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        notifier: ResourceBuildEventNotifier,
    ) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier

    async def require_build(
        self,
        build_id: str,
        scope: OwnerScope,
    ) -> ResourceBuild:
        async with self._uow_factory() as uow:
            return await self._owned_build(
                uow,
                build_id,
                scope,
                for_update=False,
            )

    async def validate_cursor(
        self,
        build_id: str,
        after_seq: int,
        scope: OwnerScope,
    ) -> ResourceBuild:
        if after_seq < 0:
            raise BadRequestError(
                "resource build event cursor must be non-negative"
            )
        async with self._uow_factory() as uow:
            build = await self._owned_build(
                uow,
                build_id,
                scope,
                for_update=False,
            )
            self._validate_cursor_against_build(after_seq, build)
            return build

    async def append_event(
        self,
        build_id: str,
        *,
        phase: str | None,
        state: BuildState,
        progress: float = 0.0,
        payload: dict[str, Any] | None = None,
        scope: OwnerScope,
        resource_kind: ResourceKind | None = None,
        resource_id: str | None = None,
        version_id: str | None = None,
    ) -> ResourceBuildEvent:
        try:
            normalized_state = BuildState(state)
        except ValueError as exc:
            raise BadRequestError("invalid resource build state") from exc
        if not 0.0 <= progress <= 1.0:
            raise BadRequestError(
                "resource build event progress must be between 0 and 1"
            )
        if phase is not None and len(phase) > 64:
            raise BadRequestError("resource build event phase is too long")
        event_payload = dict(payload or {})
        return await self._append_event(
            build_id,
            phase=phase,
            state=normalized_state,
            progress=progress,
            payload=event_payload,
            scope=scope,
            resource_kind=resource_kind,
            resource_id=resource_id,
            version_id=version_id,
            authoritative=False,
        )

    async def append_event_authoritative(
        self,
        build_id: str,
        *,
        phase: str | None,
        state: BuildState,
        progress: float = 0.0,
        payload: dict[str, Any] | None = None,
        resource_kind: ResourceKind,
        resource_id: str,
        version_id: str,
    ) -> ResourceBuildEvent:
        """Append an internal event without requiring the owner row to exist.

        This seam is deliberately identity-closed and is reserved for worker
        repair of orphaned/corrupt builds. HTTP callers continue through the
        owner-scoped ``append_event`` path.
        """
        try:
            normalized_state = BuildState(state)
            normalized_kind = ResourceKind(resource_kind)
        except ValueError as exc:
            raise BadRequestError("invalid resource build state or kind") from exc
        if not 0.0 <= progress <= 1.0:
            raise BadRequestError(
                "resource build event progress must be between 0 and 1"
            )
        if phase is not None and len(phase) > 64:
            raise BadRequestError("resource build event phase is too long")
        if not resource_id or not version_id:
            raise BadRequestError(
                "authoritative resource build event requires exact identity"
            )
        return await self._append_event(
            build_id,
            phase=phase,
            state=normalized_state,
            progress=progress,
            payload=dict(payload or {}),
            scope=None,
            resource_kind=normalized_kind,
            resource_id=resource_id,
            version_id=version_id,
            authoritative=True,
        )

    async def _append_event(
        self,
        build_id: str,
        *,
        phase: str | None,
        state: BuildState,
        progress: float,
        payload: dict[str, Any],
        scope: OwnerScope | None,
        resource_kind: ResourceKind | None,
        resource_id: str | None,
        version_id: str | None,
        authoritative: bool,
    ) -> ResourceBuildEvent:
        appended = True

        async with self._uow_factory() as uow:
            if authoritative:
                build = await uow.resource_governance.get_build(
                    build_id,
                    for_update=True,
                )
                if build is None:
                    raise NotFoundError("resource build not found")
            else:
                assert scope is not None
                build = await self._owned_build(
                    uow,
                    build_id,
                    scope,
                    for_update=True,
                )
            self._validate_expected_identity(
                build,
                resource_kind=resource_kind,
                resource_id=resource_id,
                version_id=version_id,
            )
            if build_phase_regresses(
                build.resource_kind,
                build.phase,
                phase,
            ):
                raise BadRequestError(
                    "resource build event phase cannot move backwards"
                )
            candidate = ResourceBuildEvent(
                build_id=build_id,
                seq=0,
                phase=phase,
                state=state,
                progress=progress,
                payload=payload,
            )

            if build.state in _TERMINAL_STATES:
                previous = await uow.resource_governance.get_event(
                    build_id,
                    build.last_event_seq,
                )
                if (
                    previous is not None
                    and _same_event_content(previous, candidate)
                ):
                    event = previous
                    appended = False
                else:
                    raise ConflictError(
                        "terminal resource build rejects further events"
                    )
            else:
                if state not in _ALLOWED_TRANSITIONS[build.state]:
                    raise ConflictError(
                        "invalid resource build state transition "
                        f"{build.state.value}->{state.value}"
                    )
                if progress < build.progress:
                    raise BadRequestError(
                        "resource build event progress cannot move backwards"
                    )
                try:
                    seq = await uow.resource_governance.append_event(
                        build_id,
                        candidate,
                    )
                except ValueError as exc:
                    message = str(exc)
                    if "terminal" in message or "transition" in message:
                        raise ConflictError(message) from exc
                    raise BadRequestError(message) from exc
                event = await uow.resource_governance.get_event(build_id, seq)
                if event is None:
                    raise RuntimeError(
                        "appended resource build event was not readable "
                        "inside its transaction"
                    )

        # The UoW context has committed before this advisory notification.
        if appended:
            try:
                await self._notifier.publish(build_id, event.seq)
            except Exception as exc:
                # PostgreSQL replay/heartbeat polling makes notification loss
                # recoverable. Never roll back or misreport a committed append.
                logger.warning(
                    "Resource build notification failed build=%s seq=%s: %s",
                    build_id,
                    event.seq,
                    exc,
                )
        return event

    async def get_event(
        self,
        build_id: str,
        seq: int,
        *,
        scope: OwnerScope,
    ) -> ResourceBuildEvent | None:
        if seq < 1:
            raise BadRequestError(
                "resource build event sequence must be positive"
            )
        async with self._uow_factory() as uow:
            await self._owned_build(
                uow,
                build_id,
                scope,
                for_update=False,
            )
            return await uow.resource_governance.get_event(build_id, seq)

    async def list_events(
        self,
        build_id: str,
        after_seq: int,
        limit: int,
        scope: OwnerScope,
    ) -> list[ResourceBuildEvent]:
        if after_seq < 0:
            raise BadRequestError(
                "resource build event cursor must be non-negative"
            )
        if not 1 <= limit <= MAX_EVENT_PAGE_SIZE:
            raise BadRequestError(
                f"resource build event limit must be between 1 and "
                f"{MAX_EVENT_PAGE_SIZE}"
            )
        async with self._uow_factory() as uow:
            build = await self._owned_build(
                uow,
                build_id,
                scope,
                for_update=False,
            )
            self._validate_cursor_against_build(after_seq, build)
            return await uow.resource_governance.list_events(
                build_id,
                after_seq,
                limit,
            )

    @staticmethod
    def _validate_cursor_against_build(
        after_seq: int,
        build: ResourceBuild,
    ) -> None:
        if after_seq > build.last_event_seq:
            raise BadRequestError(
                "resource build event cursor is ahead of durable build cursor"
            )

    async def _owned_build(
        self,
        uow: IUnitOfWork,
        build_id: str,
        scope: OwnerScope,
        *,
        for_update: bool,
    ) -> ResourceBuild:
        build = await uow.resource_governance.get_build(
            build_id,
            for_update=for_update,
        )
        if build is None:
            raise NotFoundError(
                "resource build not found in owner scope"
            )

        if build.resource_kind == ResourceKind.KNOWLEDGE_BASE:
            resource = await uow.knowledge_base.get_kb(
                build.resource_id,
                scope=scope,
            )
        elif build.resource_kind == ResourceKind.CODEBASE:
            resource = await uow.codebase.get_by_id(
                build.resource_id,
                scope=scope,
            )
        else:  # pragma: no cover - enum validation is a persistence invariant
            resource = None
        if resource is None or resource.id != build.resource_id:
            # Deliberately hide whether a foreign build exists.
            raise NotFoundError(
                "resource build not found in owner scope"
            )
        return build

    @staticmethod
    def _validate_expected_identity(
        build: ResourceBuild,
        *,
        resource_kind: ResourceKind | None,
        resource_id: str | None,
        version_id: str | None,
    ) -> None:
        try:
            normalized_kind = (
                ResourceKind(resource_kind)
                if resource_kind is not None
                else None
            )
        except ValueError as exc:
            raise BadRequestError("invalid resource kind") from exc
        if normalized_kind is not None and normalized_kind != build.resource_kind:
            raise BadRequestError(
                "resource build kind does not match expected resource"
            )
        if resource_id is not None and resource_id != build.resource_id:
            raise BadRequestError(
                "resource build does not match expected resource"
            )
        if version_id is not None and version_id != build.version_id:
            raise BadRequestError(
                "resource build does not match expected version"
            )


def _same_event_content(
    stored: ResourceBuildEvent,
    candidate: ResourceBuildEvent,
) -> bool:
    return (
        stored.build_id == candidate.build_id
        and stored.phase == candidate.phase
        and stored.state == candidate.state
        and stored.progress == candidate.progress
        and stored.payload == candidate.payload
    )
