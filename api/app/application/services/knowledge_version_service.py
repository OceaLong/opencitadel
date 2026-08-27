"""Owner-scoped ResourceVersionProvider for knowledge bases."""

from datetime import datetime

from app.application.services.resource_version_provider import (
    OwnerScopedVersionProvider,
)
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_bindings import ResourceKind
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork


class KnowledgeVersionService(OwnerScopedVersionProvider[KnowledgeBase, KnowledgeBaseVersion]):
    kind = ResourceKind.KNOWLEDGE_BASE
    _resource_label = "knowledge base"
    _version_label = "knowledge-base version"
    _cursor_label = "knowledge-version"

    async def _get_resource(
        self, uow: IUnitOfWork, resource_id: str, scope: OwnerScope
    ) -> KnowledgeBase | None:
        return await uow.knowledge_base.get_kb(resource_id, scope=scope)

    async def _get_version(
        self, uow: IUnitOfWork, version_id: str, resource_id: str
    ) -> KnowledgeBaseVersion | None:
        return await uow.knowledge_version.get_version(
            version_id,
            knowledge_base_id=resource_id,
        )

    async def _list_page(
        self,
        uow: IUnitOfWork,
        resource_id: str,
        *,
        limit: int,
        before: tuple[datetime, str] | None,
    ) -> list[KnowledgeBaseVersion]:
        return await uow.knowledge_version.list_versions(
            resource_id,
            limit=limit,
            before=before,
        )

    @classmethod
    def _is_published(cls, version: KnowledgeBaseVersion) -> bool:
        return version.published_at is not None and version.state in {
            KnowledgeVersionState.READY,
            KnowledgeVersionState.DEGRADED,
        }

    @classmethod
    def _is_degraded(cls, version: KnowledgeBaseVersion) -> bool:
        return version.state is KnowledgeVersionState.DEGRADED

    @classmethod
    def _resource_id_of(cls, version: KnowledgeBaseVersion) -> str:
        return version.knowledge_base_id
