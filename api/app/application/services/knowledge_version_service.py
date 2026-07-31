#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Owner-scoped ResourceVersionProvider for knowledge bases."""
from collections.abc import Callable
from datetime import datetime

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork


class KnowledgeVersionService:
    kind = ResourceKind.KNOWLEDGE_BASE
    _PAGE_SIZE = 500

    def __init__(self, *, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def resolve_published_version(
        self,
        resource_id: str,
        requested_version_id: str | None,
        scope: OwnerScope,
    ) -> PublishedResourceVersion:
        async with self._uow_factory() as uow:
            knowledge_base = await uow.knowledge_base.get_kb(
                resource_id, scope=scope
            )
            if knowledge_base is None:
                raise NotFoundError(
                    "knowledge base not found in owner scope"
                )

            version_id = requested_version_id or knowledge_base.active_version_id
            if version_id is None:
                raise BadRequestError(
                    "knowledge base has no published version"
                )
            version = await uow.knowledge_version.get_version(
                version_id,
                knowledge_base_id=resource_id,
            )
            if version is None:
                if requested_version_id is None:
                    raise BadRequestError(
                        "active knowledge-base version is not published"
                    )
                raise NotFoundError(
                    "knowledge-base version not found in owner scope"
                )
            return self._published_projection(version)

    async def list_published_versions(
        self,
        resource_id: str,
        scope: OwnerScope,
    ) -> list[PublishedResourceVersion]:
        async with self._uow_factory() as uow:
            knowledge_base = await uow.knowledge_base.get_kb(
                resource_id, scope=scope
            )
            if knowledge_base is None:
                raise NotFoundError(
                    "knowledge base not found in owner scope"
                )

            versions: list[KnowledgeBaseVersion] = []
            before: tuple[datetime, str] | None = None
            seen_ids: set[str] = set()
            while True:
                page = await uow.knowledge_version.list_versions(
                    resource_id,
                    limit=self._PAGE_SIZE,
                    before=before,
                )
                for version in page:
                    if version.id not in seen_ids:
                        seen_ids.add(version.id)
                        versions.append(version)
                if len(page) < self._PAGE_SIZE:
                    break
                next_before = (page[-1].created_at, page[-1].id)
                if next_before == before:
                    raise RuntimeError(
                        "knowledge-version pagination cursor did not advance"
                    )
                before = next_before

            return [
                self._published_projection(version)
                for version in versions
                if self._is_published(version)
            ]

    @staticmethod
    def _is_published(version: KnowledgeBaseVersion) -> bool:
        return (
            version.published_at is not None
            and version.state
            in {
                KnowledgeVersionState.READY,
                KnowledgeVersionState.DEGRADED,
            }
        )

    @classmethod
    def _published_projection(
        cls,
        version: KnowledgeBaseVersion,
    ) -> PublishedResourceVersion:
        if not cls._is_published(version):
            raise BadRequestError(
                "knowledge-base version is not published"
            )
        reasons = list(version.degraded_reasons)
        if (
            version.state is KnowledgeVersionState.READY
            and reasons
        ) or (
            version.state is KnowledgeVersionState.DEGRADED
            and (
                not reasons
                or any(
                    not isinstance(reason, str) or not reason.strip()
                    for reason in reasons
                )
            )
        ):
            raise BadRequestError(
                "knowledge-base version has inconsistent degradation metadata"
            )
        degraded = version.state is KnowledgeVersionState.DEGRADED
        return PublishedResourceVersion(
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id=version.knowledge_base_id,
            version_id=version.id,
            state=BuildState.DEGRADED if degraded else BuildState.SUCCEEDED,
            published=True,
            degraded=degraded,
            capabilities=dict(version.capabilities),
            degraded_reasons=reasons,
        )
