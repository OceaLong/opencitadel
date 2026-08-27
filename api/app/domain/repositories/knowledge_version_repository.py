"""Persistence contract for immutable knowledge-base versions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
)
from app.domain.repositories.patch import UNSET, UnsetType


@dataclass(frozen=True)
class KnowledgeVersionGCResult:
    """Secret-free, deterministic counters from one bounded GC transaction."""

    collected_version_ids: tuple[str, ...] = ()
    deleted_versions: int = 0
    deleted_manifests: int = 0
    deleted_revisions: int = 0
    deleted_chunks: int = 0
    reclaimed_logical_bytes: int = 0
    deleted_entities: int = 0
    deleted_relations: int = 0
    deleted_entity_refs: int = 0
    retained_shared_revisions: int = 0
    protected_active_versions: int = 0
    protected_bound_versions: int = 0
    protected_building_versions: int = 0
    protected_age_versions: int = 0
    protected_retention_versions: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "collected_version_ids",
            tuple(self.collected_version_ids),
        )
        counters = (
            self.deleted_versions,
            self.deleted_manifests,
            self.deleted_revisions,
            self.deleted_chunks,
            self.reclaimed_logical_bytes,
            self.deleted_entities,
            self.deleted_relations,
            self.deleted_entity_refs,
            self.retained_shared_revisions,
            self.protected_active_versions,
            self.protected_bound_versions,
            self.protected_building_versions,
            self.protected_age_versions,
            self.protected_retention_versions,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counters
        ):
            raise ValueError("knowledge-version GC counters must be non-negative")
        if self.deleted_versions != len(self.collected_version_ids):
            raise ValueError("deleted version count must match collected version identities")

    @property
    def retained_reference_count(self) -> int:
        return (
            self.protected_bound_versions
            + self.protected_building_versions
            + self.retained_shared_revisions
        )

    def metrics(self) -> dict[str, int]:
        """Return stable, non-content metrics suitable for logs and audits."""
        return {
            "collected_versions": len(self.collected_version_ids),
            "deleted_chunks": self.deleted_chunks,
            "deleted_entities": self.deleted_entities,
            "deleted_entity_refs": self.deleted_entity_refs,
            "deleted_manifests": self.deleted_manifests,
            "deleted_relations": self.deleted_relations,
            "deleted_revisions": self.deleted_revisions,
            "deleted_versions": self.deleted_versions,
            "protected_building_versions": self.protected_building_versions,
            "protected_active_versions": self.protected_active_versions,
            "protected_age_versions": self.protected_age_versions,
            "protected_bound_versions": self.protected_bound_versions,
            "protected_retention_versions": (self.protected_retention_versions),
            "reclaimed_logical_bytes": self.reclaimed_logical_bytes,
            "retained_reference_count": self.retained_reference_count,
            "retained_shared_revisions": self.retained_shared_revisions,
        }


class KnowledgeVersionRepository(Protocol):
    async def collect_garbage(
        self,
        *,
        retain_count: int,
        older_than: datetime,
        batch_size: int,
    ) -> KnowledgeVersionGCResult:
        """Collect one bounded, reference-safe batch in the current UoW."""
        ...

    async def create_candidate(
        self,
        version: KnowledgeBaseVersion,
    ) -> KnowledgeBaseVersion:
        """Persist a building candidate after validating parent/build closure."""
        ...

    async def get_version(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ) -> KnowledgeBaseVersion | None:
        """Return one version, optionally closed over one knowledge base."""
        ...

    async def list_versions(
        self,
        knowledge_base_id: str,
        *,
        limit: int = 500,
        before: tuple[datetime, str] | None = None,
    ) -> list[KnowledgeBaseVersion]:
        """Return deterministic newest-first version history."""
        ...

    async def get_manifest(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ) -> list[KnowledgeVersionDocument]:
        """Return a same-KB manifest in ordinal order."""
        ...

    async def get_build_candidate(
        self,
        build_id: str,
    ) -> tuple[KnowledgeBaseVersion, list[KnowledgeVersionDocument]] | None:
        """Resolve the exact build-backed candidate and complete manifest."""
        ...

    async def get_active_candidate(
        self,
        knowledge_base_id: str,
    ) -> KnowledgeBaseVersion | None:
        """Return the sole building candidate for a knowledge base."""
        ...

    async def transition_document(
        self,
        version_id: str,
        document_id: str,
        *,
        knowledge_base_id: str,
        state: DocumentRevisionState,
        parsed_blocks: list[dict] | UnsetType | None = UNSET,
        page_count: int | UnsetType | None = UNSET,
        error: str | UnsetType | None = UNSET,
        warning: str | UnsetType | None = UNSET,
    ) -> KnowledgeVersionDocument:
        """Persist a closed revision/manifest transition for one candidate."""
        ...

    async def add_revision(
        self,
        revision: KnowledgeDocumentRevision,
        *,
        knowledge_base_id: str,
    ) -> KnowledgeDocumentRevision:
        """Persist one immutable revision closed over its document owner."""
        ...

    async def get_revisions(
        self,
        revision_ids: list[str],
        *,
        knowledge_base_id: str,
    ) -> dict[str, KnowledgeDocumentRevision]:
        """Return only revisions whose logical documents belong to this KB."""
        ...

    async def get_revision_by_digest(
        self,
        document_id: str,
        source_digest: str,
        *,
        knowledge_base_id: str,
    ) -> KnowledgeDocumentRevision | None:
        """Return an exact historical same-document content revision."""
        ...

    async def add_manifest(
        self,
        version_id: str,
        documents: list[KnowledgeVersionDocument],
        *,
        knowledge_base_id: str,
    ) -> None:
        """Persist one complete immutable candidate manifest."""
        ...

    async def publish_candidate(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
        expected_active_version_id: str | None,
        state: KnowledgeVersionState,
        capabilities: dict[str, bool],
        degraded_reasons: list[str],
        metrics: dict[str, int | float],
    ) -> bool:
        """Atomically publish a building candidate with a locked KB CAS."""
        ...

    async def fail_candidate(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
        metrics: dict[str, int | float] | None = None,
    ) -> bool:
        """Move a building candidate to failed without changing the active pin."""
        ...

    async def update_candidate_metrics(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
        metrics: dict[str, Any],
    ) -> bool:
        """Merge artifact-processing checkpoints into a building candidate."""
        ...
