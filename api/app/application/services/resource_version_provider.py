#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared owner-scoped ResourceVersionProvider skeleton for versioned resources."""
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import ClassVar, Generic, TypeVar

from app.domain.errors import BadRequestError, NotFoundError
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork

TResource = TypeVar("TResource")
TVersion = TypeVar("TVersion")


class OwnerScopedVersionProvider(ABC, Generic[TResource, TVersion]):
    kind: ClassVar[ResourceKind]
    _PAGE_SIZE = 500
    _resource_label: ClassVar[str]   # "knowledge base" / "codebase"
    _version_label: ClassVar[str]    # "knowledge-base version" / "codebase version"
    _cursor_label: ClassVar[str]     # "knowledge-version" / "codebase-version"

    def __init__(self, *, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    # --- subclass hooks -------------------------------------------------
    @abstractmethod
    async def _get_resource(
        self, uow: IUnitOfWork, resource_id: str, scope: OwnerScope
    ) -> TResource | None: ...

    @abstractmethod
    async def _get_version(
        self, uow: IUnitOfWork, version_id: str, resource_id: str
    ) -> TVersion | None: ...

    @abstractmethod
    async def _list_page(
        self,
        uow: IUnitOfWork,
        resource_id: str,
        *,
        limit: int,
        before: tuple[datetime, str] | None,
    ) -> list[TVersion]: ...

    @classmethod
    @abstractmethod
    def _is_published(cls, version: TVersion) -> bool: ...

    @classmethod
    @abstractmethod
    def _is_degraded(cls, version: TVersion) -> bool: ...

    @classmethod
    @abstractmethod
    def _resource_id_of(cls, version: TVersion) -> str: ...

    # --- shared flow ----------------------------------------------------
    async def resolve_published_version(
        self,
        resource_id: str,
        requested_version_id: str | None,
        scope: OwnerScope,
    ) -> PublishedResourceVersion:
        async with self._uow_factory() as uow:
            resource = await self._get_resource(uow, resource_id, scope)
            if resource is None:
                raise NotFoundError(f"{self._resource_label} not found in owner scope")
            version_id = requested_version_id or resource.active_version_id
            if version_id is None:
                raise BadRequestError(f"{self._resource_label} has no published version")
            version = await self._get_version(uow, version_id, resource_id)
            if version is None:
                if requested_version_id is None:
                    raise BadRequestError(f"active {self._version_label} is not published")
                raise NotFoundError(f"{self._version_label} not found in owner scope")
            return self._published_projection(version)

    async def list_published_versions(
        self, resource_id: str, scope: OwnerScope
    ) -> list[PublishedResourceVersion]:
        async with self._uow_factory() as uow:
            resource = await self._get_resource(uow, resource_id, scope)
            if resource is None:
                raise NotFoundError(f"{self._resource_label} not found in owner scope")
            versions: list[TVersion] = []
            before: tuple[datetime, str] | None = None
            seen_ids: set[str] = set()
            while True:
                page = await self._list_page(
                    uow, resource_id, limit=self._PAGE_SIZE, before=before
                )
                for version in page:
                    vid = self._resource_version_id(version)
                    if vid not in seen_ids:
                        seen_ids.add(vid)
                        versions.append(version)
                if len(page) < self._PAGE_SIZE:
                    break
                next_before = (page[-1].created_at, page[-1].id)
                if next_before == before:
                    raise RuntimeError(
                        f"{self._cursor_label} pagination cursor did not advance"
                    )
                before = next_before
            return [
                self._published_projection(v) for v in versions if self._is_published(v)
            ]

    @staticmethod
    def _resource_version_id(version: TVersion) -> str:
        return version.id

    @classmethod
    def _published_projection(cls, version: TVersion) -> PublishedResourceVersion:
        if not cls._is_published(version):
            raise BadRequestError(f"{cls._version_label} is not published")
        reasons = list(version.degraded_reasons)
        degraded = cls._is_degraded(version)
        if (not degraded and reasons) or (
            degraded
            and (
                not reasons
                or any(not isinstance(r, str) or not r.strip() for r in reasons)
            )
        ):
            raise BadRequestError(
                f"{cls._version_label} has inconsistent degradation metadata"
            )
        return PublishedResourceVersion(
            resource_kind=cls.kind,
            resource_id=cls._resource_id_of(version),
            version_id=version.id,
            state=BuildState.DEGRADED if degraded else BuildState.SUCCEEDED,
            published=True,
            degraded=degraded,
            capabilities=dict(version.capabilities),
            degraded_reasons=reasons,
        )
