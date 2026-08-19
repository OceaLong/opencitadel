#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Transactional immutable resource bindings for session turns."""
from collections.abc import Callable

from app.domain.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.models.knowledge_version import KnowledgeVersionState
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.resource_version_provider import (
    ResourceVersionProvider,
    ResourceVersionProviderNotRegisteredError,
    ResourceVersionProviderRegistry,
)


class ResourceBindingService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        providers: ResourceVersionProviderRegistry,
    ) -> None:
        self._uow_factory = uow_factory
        self._providers = providers

    async def bind_initial(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        requested_version_id: str | None,
        scope: OwnerScope,
        *,
        actor_id: str | None = None,
    ) -> SessionResourceBinding:
        kind = ResourceKind(resource_kind)
        if actor_id is not None and actor_id != scope.user_id:
            raise ForbiddenError("binding actor must match the owner scope")
        await self._assert_owned_session(session_id, scope)
        provider = self._provider_for(kind)
        resolved = await provider.resolve_published_version(
            resource_id,
            requested_version_id,
            scope,
        )
        self._validate_provider_result(
            resolved,
            kind=kind,
            resource_id=resource_id,
            requested_version_id=requested_version_id,
        )

        async with self._uow_factory() as uow:
            session = await uow.session.lock_by_id(
                session_id,
                scope=scope,
            )
            if session is None:
                raise NotFoundError("session not found in owner scope")
            current = await uow.resource_governance.get_current_binding(
                session_id,
                kind,
                for_update=True,
            )
            if current is not None:
                if current.resource_id != resource_id:
                    raise ConflictError(
                        "session resource kind is already bound to another "
                        "resource"
                    )
                if (
                    requested_version_id is None
                    or current.version_id == resolved.version_id
                ):
                    return current
                raise ConflictError(
                    "initial binding is already pinned to another version"
                )

            binding = SessionResourceBinding(
                session_id=session_id,
                resource_kind=kind,
                resource_id=resource_id,
                version_id=resolved.version_id,
                bound_by=actor_id or scope.user_id,
            )
            await self._lock_final_knowledge_version_reference(
                uow,
                resolved=resolved,
                scope=scope,
            )
            return await uow.resource_governance.add_binding(binding)

    async def bind_initial_resolved(
        self,
        uow: IUnitOfWork,
        *,
        session_id: str,
        resolved: PublishedResourceVersion,
        scope: OwnerScope,
        actor_id: str,
    ) -> SessionResourceBinding:
        """Add a pre-validated pin in the caller's session transaction."""
        if actor_id != scope.user_id:
            raise ForbiddenError("binding actor must match the owner scope")
        self._validate_provider_result(
            resolved,
            kind=resolved.resource_kind,
            resource_id=resolved.resource_id,
            requested_version_id=resolved.version_id,
        )
        current = await uow.resource_governance.get_current_binding(
            session_id, resolved.resource_kind, for_update=True,
        )
        if current is not None:
            raise ConflictError("session resource kind is already bound")
        await self._lock_final_knowledge_version_reference(
            uow,
            resolved=resolved,
            scope=scope,
        )
        return await uow.resource_governance.add_binding(SessionResourceBinding(
            session_id=session_id,
            resource_kind=resolved.resource_kind,
            resource_id=resolved.resource_id,
            version_id=resolved.version_id,
            bound_by=actor_id,
        ))

    async def current(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        scope: OwnerScope,
    ) -> SessionResourceBinding:
        kind = ResourceKind(resource_kind)
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(
                session_id,
                scope=scope,
            )
            if session is None:
                raise NotFoundError("session not found in owner scope")
            binding = await uow.resource_governance.get_current_binding(
                session_id,
                kind,
            )
            if binding is None:
                raise NotFoundError("current resource binding not found")
            return binding

    async def current_version_id(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        scope: OwnerScope,
    ) -> str:
        return (
            await self.current(session_id, resource_kind, scope)
        ).version_id

    async def current_bindings(
        self,
        session_id: str,
        scope: OwnerScope,
    ) -> list[SessionResourceBinding]:
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id, scope=scope)
            if session is None:
                raise NotFoundError("session not found in owner scope")
            return await uow.resource_governance.list_current_bindings(session_id)

    async def available_versions(
        self, session_id: str, resource_kind: ResourceKind, scope: OwnerScope,
    ) -> list[PublishedResourceVersion]:
        binding = await self.current(session_id, resource_kind, scope)
        provider = self._provider_for(resource_kind)
        versions = await provider.list_published_versions(binding.resource_id, scope)
        for version in versions:
            self._validate_provider_result(
                version, kind=resource_kind, resource_id=binding.resource_id,
                requested_version_id=None,
            )
        return versions

    async def history(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        scope: OwnerScope,
    ) -> list[SessionResourceBinding]:
        kind = ResourceKind(resource_kind)
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(
                session_id,
                scope=scope,
            )
            if session is None:
                raise NotFoundError("session not found in owner scope")
            return await uow.resource_governance.list_bindings(
                session_id,
                kind,
            )

    async def upgrade(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        target_version_id: str,
        *,
        actor_id: str,
        scope: OwnerScope,
    ) -> SessionResourceBinding:
        kind = ResourceKind(resource_kind)
        if actor_id != scope.user_id:
            raise ForbiddenError("binding actor must match the owner scope")
        if not target_version_id:
            raise BadRequestError("target version is required")
        await self._assert_owned_session(session_id, scope)
        provider = self._provider_for(kind)

        async with self._uow_factory() as uow:
            session = await uow.session.lock_by_id(
                session_id,
                scope=scope,
            )
            if session is None:
                raise NotFoundError("session not found in owner scope")
            current = await uow.resource_governance.get_current_binding(
                session_id,
                kind,
                for_update=True,
            )
            if current is None:
                raise NotFoundError("current resource binding not found")

            resolved = await provider.resolve_published_version(
                current.resource_id,
                target_version_id,
                scope,
            )
            self._validate_provider_result(
                resolved,
                kind=kind,
                resource_id=current.resource_id,
                requested_version_id=target_version_id,
            )
            if resolved.version_id == current.version_id:
                return current

            await self._lock_final_knowledge_version_reference(
                uow,
                resolved=resolved,
                scope=scope,
            )
            replacement = SessionResourceBinding(
                session_id=session_id,
                resource_kind=kind,
                resource_id=current.resource_id,
                version_id=resolved.version_id,
                supersedes_binding_id=current.id,
                bound_by=actor_id,
            )
            return (
                await uow.resource_governance.replace_current_binding(
                    current,
                    replacement,
                )
            )

    @staticmethod
    async def _lock_final_knowledge_version_reference(
        uow: IUnitOfWork,
        *,
        resolved: PublishedResourceVersion,
        scope: OwnerScope,
    ) -> None:
        """Serialize the final KB pin with publish and version GC.

        Provider resolution can happen in another UoW. The KB row is the
        shared mutex: publish, binding insertion, and GC all acquire it before
        their final version decision, then access the version row.
        """
        if resolved.resource_kind is not ResourceKind.KNOWLEDGE_BASE:
            return
        knowledge_base = await uow.knowledge_base.get_kb_for_update(
            resolved.resource_id,
            scope=scope,
        )
        if knowledge_base is None:
            raise ConflictError("knowledge base is no longer available")
        version = await uow.knowledge_version.get_version(
            resolved.version_id,
            knowledge_base_id=resolved.resource_id,
        )
        if (
            version is None
            or version.published_at is None
            or version.state
            not in {
                KnowledgeVersionState.READY,
                KnowledgeVersionState.DEGRADED,
            }
        ):
            raise ConflictError(
                "knowledge-base version is no longer bindable"
            )

    async def _assert_owned_session(
        self,
        session_id: str,
        scope: OwnerScope,
    ) -> None:
        async with self._uow_factory() as uow:
            if (
                await uow.session.get_metadata(
                    session_id,
                    scope=scope,
                )
                is None
            ):
                raise NotFoundError("session not found in owner scope")

    def _provider_for(
        self,
        kind: ResourceKind,
    ) -> ResourceVersionProvider:
        try:
            return self._providers.get(kind)
        except ResourceVersionProviderNotRegisteredError as exc:
            raise BadRequestError(str(exc)) from exc

    @staticmethod
    def _validate_provider_result(
        resolved: PublishedResourceVersion,
        *,
        kind: ResourceKind,
        resource_id: str,
        requested_version_id: str | None,
    ) -> None:
        if resolved.resource_kind != kind:
            raise BadRequestError(
                "resource version provider returned a kind mismatch"
            )
        if resolved.resource_id != resource_id:
            raise BadRequestError(
                "resource version provider returned a foreign resource"
            )
        if (
            requested_version_id is not None
            and resolved.version_id != requested_version_id
        ):
            raise BadRequestError(
                "resource version provider returned a foreign version"
            )
        if (
            not resolved.published
            or resolved.state
            not in {BuildState.SUCCEEDED, BuildState.DEGRADED}
        ):
            raise BadRequestError("resource version is not published")
