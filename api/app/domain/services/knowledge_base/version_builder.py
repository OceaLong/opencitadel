"""Copy-on-write construction of immutable knowledge-base candidates."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy.exc import IntegrityError

from app.domain.errors import ConflictError, NotFoundError
from app.domain.models.knowledge_base import (
    DocStatus,
    KBSourceType,
    KBStatus,
    KnowledgeBase,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
)
from app.domain.models.resource_bindings import (
    ResourceBuildIntent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.knowledge_base.url_guard import validate_public_url


class KnowledgeBuildOperation(StrEnum):
    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"
    REINDEX = "reindex"


class KnowledgeBuildSource(BaseModel):
    """Validated immutable source material prepared before durable writes."""

    model_config = ConfigDict(frozen=True)

    document_id: str | None = None
    title: str
    source_type: KBSourceType
    source_ref: str
    source_identity: str
    source_digest: str
    mime: str = ""
    file_id: str | None = None

    @field_validator(
        "title",
        "source_ref",
        "source_identity",
        "source_digest",
    )
    @classmethod
    def _require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge build source fields cannot be empty")
        return normalized

    @field_validator("source_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("source_digest must be a SHA-256 hex digest")
        return normalized

    @field_validator("document_id", "file_id")
    @classmethod
    def _normalize_optional_identity(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("optional source identities cannot be empty")
        return normalized

    @field_validator("mime")
    @classmethod
    def _normalize_mime(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def _validate_canonical_source_shape(self) -> KnowledgeBuildSource:
        if self.source_type in {KBSourceType.UPLOAD, KBSourceType.ZIP}:
            if self.file_id is None:
                raise ValueError("file sources require an owner-scoped file id")
            if self.source_ref != _canonical_file_source_ref(self.file_id):
                raise ValueError("file source reference must be canonical for its file id")
            if self.source_identity != f"file:{self.file_id}":
                raise ValueError("file source identity must match its immutable file id")
            return self
        if self.source_type not in {
            KBSourceType.WEB,
            KBSourceType.CONFLUENCE,
            KBSourceType.FEISHU,
        }:
            raise ValueError("unsupported knowledge source type")
        if self.file_id is not None:
            raise ValueError("URL sources must not carry a file id")
        parsed = urlsplit(self.source_ref)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("URL sources require a canonical public URL")
        if self.source_identity != (f"{self.source_type.value}:{self.source_ref}"):
            raise ValueError("URL source identity must match its typed canonical URL")
        return self


class KnowledgeBuildCommand(BaseModel):
    """Canonical immutable intent for one candidate manifest."""

    model_config = ConfigDict(frozen=True)

    knowledge_base_id: str
    operation: KnowledgeBuildOperation
    sources: tuple[KnowledgeBuildSource, ...] = ()
    document_ids: tuple[str, ...] = ()
    actor_id: str
    options: tuple[tuple[str, Any], ...] = ()

    @field_validator("knowledge_base_id", "actor_id")
    @classmethod
    def _require_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge build identity cannot be empty")
        return normalized

    @field_validator("sources", mode="before")
    @classmethod
    def _canonical_sources(
        cls,
        value: Iterable[KnowledgeBuildSource | Mapping[str, Any]] | None,
    ) -> tuple[KnowledgeBuildSource, ...]:
        sources = tuple(
            item
            if isinstance(item, KnowledgeBuildSource)
            else KnowledgeBuildSource.model_validate(item)
            for item in (value or ())
        )
        identities = [item.source_identity for item in sources]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate source identities are not allowed")
        logical_ids = [item.document_id for item in sources if item.document_id is not None]
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("duplicate logical document ids are not allowed")
        return tuple(
            sorted(
                sources,
                key=lambda item: (
                    item.document_id or "",
                    item.source_identity,
                    item.source_digest,
                ),
            )
        )

    @field_validator("document_ids", mode="before")
    @classmethod
    def _canonical_document_ids(
        cls,
        value: Iterable[str] | None,
    ) -> tuple[str, ...]:
        values = tuple(sorted(item.strip() for item in (value or ())))
        if any(not item for item in values):
            raise ValueError("document ids cannot be empty")
        if len(values) != len(set(values)):
            raise ValueError("duplicate logical document ids are not allowed")
        return values

    @field_validator("options", mode="before")
    @classmethod
    def _canonical_options(
        cls,
        value: Mapping[str, Any] | Iterable[tuple[str, Any]] | None,
    ) -> tuple[tuple[str, Any], ...]:
        if value is None:
            return ()
        items = value.items() if isinstance(value, Mapping) else value
        return tuple(sorted((str(key), _freeze_option(item)) for key, item in items))

    @model_validator(mode="after")
    def _validate_shape(self) -> KnowledgeBuildCommand:
        if self.operation is KnowledgeBuildOperation.ADD:
            if not self.sources or self.document_ids:
                raise ValueError("add requires sources and no document ids")
            if any(source.document_id is not None for source in self.sources):
                raise ValueError("add does not accept a caller-supplied document id")
        elif self.operation is KnowledgeBuildOperation.REMOVE:
            if len(self.document_ids) != 1 or self.sources:
                raise ValueError("remove requires exactly one document id")
        elif self.operation is KnowledgeBuildOperation.REPLACE:
            if len(self.document_ids) != 1 or len(self.sources) != 1:
                raise ValueError("replace requires one document id and one source")
            source_id = self.sources[0].document_id
            if source_id is not None and source_id != self.document_ids[0]:
                raise ValueError("replacement source document id must match its target")
        elif self.operation is KnowledgeBuildOperation.REINDEX:
            if self.document_ids:
                raise ValueError("reindex does not accept removal ids")
            if any(item.document_id is None for item in self.sources):
                raise ValueError("reindex sources require logical document ids")
        return self

    @classmethod
    def add(
        cls,
        knowledge_base_id: str,
        sources: Sequence[KnowledgeBuildSource],
        *,
        actor_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> KnowledgeBuildCommand:
        return cls(
            knowledge_base_id=knowledge_base_id,
            operation=KnowledgeBuildOperation.ADD,
            sources=tuple(sources),
            actor_id=actor_id,
            options=options or {},
        )

    @classmethod
    def remove(
        cls,
        knowledge_base_id: str,
        document_id: str,
        *,
        actor_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> KnowledgeBuildCommand:
        return cls(
            knowledge_base_id=knowledge_base_id,
            operation=KnowledgeBuildOperation.REMOVE,
            document_ids=(document_id,),
            actor_id=actor_id,
            options=options or {},
        )

    @classmethod
    def replace(
        cls,
        knowledge_base_id: str,
        document_id: str,
        source: KnowledgeBuildSource,
        *,
        actor_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> KnowledgeBuildCommand:
        return cls(
            knowledge_base_id=knowledge_base_id,
            operation=KnowledgeBuildOperation.REPLACE,
            sources=(source,),
            document_ids=(document_id,),
            actor_id=actor_id,
            options=options or {},
        )

    @classmethod
    def reindex(
        cls,
        knowledge_base_id: str,
        sources: Sequence[KnowledgeBuildSource] = (),
        *,
        actor_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> KnowledgeBuildCommand:
        return cls(
            knowledge_base_id=knowledge_base_id,
            operation=KnowledgeBuildOperation.REINDEX,
            sources=tuple(sources),
            actor_id=actor_id,
            options=options or {},
        )

    def command_key(
        self,
        *,
        owner_identity: str,
        base_version_id: str | None,
    ) -> str:
        payload = {
            "resource_kind": ResourceKind.KNOWLEDGE_BASE.value,
            "resource_id": self.knowledge_base_id,
            "owner": owner_identity,
            "actor_id": self.actor_id,
            "base_version_id": base_version_id,
            "operation": self.operation.value,
            "document_ids": self.document_ids,
            "sources": [
                {
                    "document_id": item.document_id,
                    "title": item.title,
                    "source_type": item.source_type.value,
                    "source_ref": item.source_ref,
                    "source_identity": item.source_identity,
                    "source_digest": item.source_digest,
                    "mime": item.mime,
                    "file_id": item.file_id,
                }
                for item in self.sources
            ],
            "options": self.options,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class CandidateBuildResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource: KnowledgeBase
    version: KnowledgeBaseVersion
    build: ResourceBuildIntent
    created: bool


class KnowledgeVersionBuilder:
    """Creates a complete candidate graph in exactly one unit of work."""

    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def create_candidate(
        self,
        command: KnowledgeBuildCommand,
        *,
        scope: OwnerScope,
        before_commit: Callable[[IUnitOfWork, CandidateBuildResult], Awaitable[None]] | None = None,
    ) -> CandidateBuildResult:
        if command.actor_id != scope.user_id:
            raise NotFoundError("knowledge base not found in owner scope")
        self._validate_prepared_sources(command.sources)
        try:
            return await self._create_once(
                command,
                scope=scope,
                before_commit=before_commit,
            )
        except IntegrityError as exc:
            if not _is_active_build_uniqueness_violation(exc):
                raise
            # The failed transaction is exited/rolled back before this fresh
            # UoW reads the database-authoritative partial-unique winner.
            return await self._recover_concurrent_winner(
                command,
                scope=scope,
            )

    async def retry_candidate(
        self,
        knowledge_base_id: str,
        build_id: str,
        *,
        actor_id: str,
        scope: OwnerScope,
        before_commit: Callable[[IUnitOfWork, CandidateBuildResult], Awaitable[None]] | None = None,
    ) -> CandidateBuildResult:
        """Clone one terminal candidate into a fresh, retryable generation."""
        if actor_id != scope.user_id:
            raise NotFoundError("knowledge build not found in owner scope")
        try:
            return await self._retry_once(
                knowledge_base_id,
                build_id,
                actor_id=actor_id,
                scope=scope,
                before_commit=before_commit,
            )
        except IntegrityError as exc:
            if not _is_active_build_uniqueness_violation(exc):
                raise
            return await self._recover_retry_winner(
                knowledge_base_id,
                build_id,
                scope=scope,
            )

    async def manifest(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
        scope: OwnerScope,
    ) -> list[KnowledgeVersionDocument]:
        async with self._uow_factory() as uow:
            resource = await uow.knowledge_base.get_kb(
                knowledge_base_id,
                scope=scope,
            )
            if resource is None:
                raise NotFoundError("knowledge base version not found in owner scope")
            version = await uow.knowledge_version.get_version(
                version_id,
                knowledge_base_id=knowledge_base_id,
            )
            if version is None:
                raise NotFoundError("knowledge base version not found in owner scope")
            return await uow.knowledge_version.get_manifest(
                version_id,
                knowledge_base_id=knowledge_base_id,
            )

    async def _create_once(
        self,
        command: KnowledgeBuildCommand,
        *,
        scope: OwnerScope,
        before_commit: Callable[[IUnitOfWork, CandidateBuildResult], Awaitable[None]] | None,
    ) -> CandidateBuildResult:
        async with self._uow_factory() as uow:
            self._require_builder_wiring(uow)
            resource = await uow.knowledge_base.get_kb(
                command.knowledge_base_id,
                scope=scope,
            )
            if resource is None:
                raise NotFoundError("knowledge base not found in owner scope")
            owner_identity = _owner_identity(resource, scope)
            key = command.command_key(
                owner_identity=owner_identity,
                base_version_id=resource.active_version_id,
            )
            active = await uow.knowledge_version.get_active_candidate(resource.id)
            if active is not None:
                return await self._resolve_active(
                    uow,
                    resource,
                    active,
                    key,
                )

            base_manifest = await self._base_manifest(uow, resource)
            revisions = await uow.knowledge_version.get_revisions(
                [entry.document_revision_id for entry in base_manifest],
                knowledge_base_id=resource.id,
            )
            documents = await self._load_documents(
                uow,
                resource.id,
                base_manifest,
            )
            historical_revisions = await self._load_historical_source_revisions(
                uow,
                command.sources,
                knowledge_base_id=resource.id,
            )
            await self._validate_source_ownership(
                uow,
                command.sources,
                scope=scope,
            )
            candidate_id = str(uuid.uuid4())
            build = ResourceBuildIntent(
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id=resource.id,
                version_id=candidate_id,
                parent_version_id=resource.active_version_id,
            )
            candidate = KnowledgeBaseVersion(
                id=candidate_id,
                knowledge_base_id=resource.id,
                parent_version_id=resource.active_version_id,
                build_id=build.build_id,
                request_key=key,
            )
            manifest, new_documents, new_revisions = self._materialize(
                command,
                candidate_id=candidate_id,
                base_manifest=base_manifest,
                documents=documents,
                revisions=revisions,
                historical_revisions=historical_revisions,
            )

            for document in new_documents:
                await uow.knowledge_base.insert_document(document)
            await uow.knowledge_version.create_candidate(candidate)
            for revision in new_revisions:
                await uow.knowledge_version.add_revision(
                    revision,
                    knowledge_base_id=resource.id,
                )
            await uow.knowledge_version.add_manifest(
                candidate.id,
                manifest,
                knowledge_base_id=resource.id,
            )
            candidate_resource = resource.model_copy(
                update={
                    "status": KBStatus.PENDING,
                    "doc_count": len(manifest),
                    "error": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            await uow.knowledge_base.save_kb(candidate_resource)
            result = CandidateBuildResult(
                resource=candidate_resource,
                version=candidate,
                build=build,
                created=True,
            )
            if before_commit is not None:
                await before_commit(uow, result)
            await uow.commit()
            return result

    async def _retry_once(
        self,
        knowledge_base_id: str,
        build_id: str,
        *,
        actor_id: str,
        scope: OwnerScope,
        before_commit: Callable[[IUnitOfWork, CandidateBuildResult], Awaitable[None]] | None,
    ) -> CandidateBuildResult:
        async with self._uow_factory() as uow:
            self._require_retry_wiring(uow)
            resource = await uow.knowledge_base.get_kb_for_update(
                knowledge_base_id,
                scope=scope,
            )
            if resource is None:
                raise NotFoundError("knowledge build not found in owner scope")
            original_result = await uow.knowledge_version.get_build_candidate(build_id)
            if original_result is None:
                raise NotFoundError("knowledge build not found in owner scope")
            original, manifest = original_result
            if original.knowledge_base_id != resource.id:
                raise NotFoundError("knowledge build not found in owner scope")
            if original.state is not KnowledgeVersionState.FAILED:
                raise ConflictError("only a failed knowledge candidate can be retried")
            if original.parent_version_id != resource.active_version_id:
                raise ConflictError("knowledge build parent is not the current active version")

            candidate = original
            if candidate.published_at is not None:
                raise ConflictError("knowledge build candidate closure is malformed")
            await self._validate_retry_manifest(
                uow,
                resource.id,
                candidate.id,
                manifest,
            )
            retry_key = _retry_command_key(
                original,
                owner_identity=_owner_identity(resource, scope),
                active_version_id=resource.active_version_id,
            )
            active = await uow.knowledge_version.get_active_candidate(resource.id)
            if active is not None:
                return await self._resolve_active(
                    uow,
                    resource,
                    active,
                    retry_key,
                )

            retry_version_id = str(uuid.uuid4())
            retry_build = ResourceBuildIntent(
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id=resource.id,
                version_id=retry_version_id,
                parent_version_id=resource.active_version_id,
            )
            retry_version = KnowledgeBaseVersion(
                id=retry_version_id,
                knowledge_base_id=resource.id,
                parent_version_id=resource.active_version_id,
                build_id=retry_build.build_id,
                request_key=retry_key,
            )
            cloned_manifest = [_reuse_entry(entry, retry_version_id) for entry in manifest]
            await uow.knowledge_version.create_candidate(retry_version)
            await uow.knowledge_version.add_manifest(
                retry_version.id,
                cloned_manifest,
                knowledge_base_id=resource.id,
            )
            candidate_resource = resource.model_copy(
                update={
                    "status": KBStatus.PENDING,
                    "doc_count": len(cloned_manifest),
                    "error": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            await uow.knowledge_base.save_kb(candidate_resource)
            result = CandidateBuildResult(
                resource=candidate_resource,
                version=retry_version,
                build=retry_build,
                created=True,
            )
            if before_commit is not None:
                await before_commit(uow, result)
            await uow.commit()
            return result

    async def _recover_retry_winner(
        self,
        knowledge_base_id: str,
        build_id: str,
        *,
        scope: OwnerScope,
    ) -> CandidateBuildResult:
        async with self._uow_factory() as uow:
            resource = await uow.knowledge_base.get_kb(
                knowledge_base_id,
                scope=scope,
            )
            if resource is None:
                raise NotFoundError("knowledge build not found in owner scope")
            original_result = await uow.knowledge_version.get_build_candidate(build_id)
            if original_result is None:
                raise ConflictError("knowledge retry race lost its valid source build")
            original, _ = original_result
            if (
                original.knowledge_base_id != resource.id
                or original.state is not KnowledgeVersionState.FAILED
                or original.parent_version_id != resource.active_version_id
            ):
                raise ConflictError("knowledge retry race lost its valid source build")
            key = _retry_command_key(
                original,
                owner_identity=_owner_identity(resource, scope),
                active_version_id=resource.active_version_id,
            )
            active = await uow.knowledge_version.get_active_candidate(resource.id)
            if active is None:
                raise ConflictError("knowledge retry race ended without an active winner")
            return await self._resolve_active(
                uow,
                resource,
                active,
                key,
            )

    @staticmethod
    def _validate_prepared_sources(
        sources: Sequence[KnowledgeBuildSource],
    ) -> None:
        for source in sources:
            if source.source_type in {
                KBSourceType.WEB,
                KBSourceType.CONFLUENCE,
                KBSourceType.FEISHU,
            }:
                safe_url = validate_public_url(source.source_ref)
                if safe_url != source.source_ref:
                    raise ValueError("URL source reference must be canonical")

    @staticmethod
    def _require_builder_wiring(uow: IUnitOfWork) -> None:
        knowledge_base = getattr(uow, "knowledge_base", None)
        knowledge_version = getattr(uow, "knowledge_version", None)
        required = (
            (knowledge_base, "insert_document"),
            (knowledge_version, "create_candidate"),
            (knowledge_version, "get_revision_by_digest"),
            (knowledge_version, "add_revision"),
            (knowledge_version, "add_manifest"),
            (knowledge_version, "get_active_candidate"),
        )
        if any(
            repository is None or not callable(getattr(repository, method, None))
            for repository, method in required
        ):
            raise RuntimeError("knowledge version build wiring is unavailable")

    @staticmethod
    def _require_retry_wiring(uow: IUnitOfWork) -> None:
        knowledge_base = getattr(uow, "knowledge_base", None)
        knowledge_version = getattr(uow, "knowledge_version", None)
        required = (
            (knowledge_base, "get_kb_for_update"),
            (knowledge_base, "get_document"),
            (knowledge_base, "save_kb"),
            (knowledge_version, "get_version"),
            (knowledge_version, "get_manifest"),
            (knowledge_version, "get_revisions"),
            (knowledge_version, "create_candidate"),
            (knowledge_version, "add_manifest"),
            (knowledge_version, "get_build_candidate"),
            (knowledge_version, "get_active_candidate"),
        )
        if any(
            repository is None or not callable(getattr(repository, method, None))
            for repository, method in required
        ):
            raise RuntimeError("knowledge retry build wiring is unavailable")

    @staticmethod
    async def _validate_retry_manifest(
        uow: IUnitOfWork,
        knowledge_base_id: str,
        version_id: str,
        manifest: Sequence[KnowledgeVersionDocument],
    ) -> None:
        if any(
            entry.version_id != version_id or entry.ordinal != ordinal
            for ordinal, entry in enumerate(manifest)
        ):
            raise ConflictError("knowledge retry manifest closure is malformed")
        document_ids = [entry.document_id for entry in manifest]
        if len(document_ids) != len(set(document_ids)):
            raise ConflictError("knowledge retry manifest closure is malformed")
        revisions = await uow.knowledge_version.get_revisions(
            [entry.document_revision_id for entry in manifest],
            knowledge_base_id=knowledge_base_id,
        )
        if len(revisions) != len({entry.document_revision_id for entry in manifest}):
            raise ConflictError("knowledge retry manifest closure is malformed")
        for entry in manifest:
            document = await uow.knowledge_base.get_document(entry.document_id)
            revision = revisions.get(entry.document_revision_id)
            if (
                document is None
                or document.kb_id != knowledge_base_id
                or revision is None
                or revision.document_id != entry.document_id
            ):
                raise ConflictError("knowledge retry manifest closure is malformed")

    @staticmethod
    async def _load_historical_source_revisions(
        uow: IUnitOfWork,
        sources: Sequence[KnowledgeBuildSource],
        *,
        knowledge_base_id: str,
    ) -> dict[tuple[str, str], KnowledgeDocumentRevision]:
        historical: dict[tuple[str, str], KnowledgeDocumentRevision] = {}
        for source in sources:
            if source.document_id is None:
                continue
            revision = await uow.knowledge_version.get_revision_by_digest(
                source.document_id,
                source.source_digest,
                knowledge_base_id=knowledge_base_id,
            )
            if revision is not None:
                historical[(source.document_id, source.source_digest)] = revision
        return historical

    async def _recover_concurrent_winner(
        self,
        command: KnowledgeBuildCommand,
        *,
        scope: OwnerScope,
    ) -> CandidateBuildResult:
        async with self._uow_factory() as uow:
            resource = await uow.knowledge_base.get_kb(
                command.knowledge_base_id,
                scope=scope,
            )
            if resource is None:
                raise NotFoundError("knowledge base not found in owner scope")
            key = command.command_key(
                owner_identity=_owner_identity(resource, scope),
                base_version_id=resource.active_version_id,
            )
            active = await uow.knowledge_version.get_active_candidate(resource.id)
            if active is None:
                raise ConflictError("knowledge build race ended without an active winner")
            return await self._resolve_active(
                uow,
                resource,
                active,
                key,
            )

    @staticmethod
    async def _resolve_active(
        uow: IUnitOfWork,
        resource: KnowledgeBase,
        active: KnowledgeBaseVersion,
        command_key: str,
    ) -> CandidateBuildResult:
        if active.request_key != command_key:
            raise ConflictError("a different knowledge build command is already active")
        if active.knowledge_base_id != resource.id:
            raise ConflictError("active knowledge build has no matching candidate version")
        candidate_resource = resource.model_copy(
            update={
                "status": KBStatus.PENDING,
                "error": None,
            }
        )
        return CandidateBuildResult(
            resource=candidate_resource,
            version=active,
            build=ResourceBuildIntent(
                build_id=active.build_id,
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id=resource.id,
                version_id=active.id,
                parent_version_id=active.parent_version_id,
            ),
            created=False,
        )

    @staticmethod
    async def _base_manifest(
        uow: IUnitOfWork,
        resource: KnowledgeBase,
    ) -> list[KnowledgeVersionDocument]:
        if resource.active_version_id is None:
            return []
        version = await uow.knowledge_version.get_version(
            resource.active_version_id,
            knowledge_base_id=resource.id,
        )
        if version is None:
            raise ConflictError("knowledge base active version is not readable")
        return await uow.knowledge_version.get_manifest(
            version.id,
            knowledge_base_id=resource.id,
        )

    @staticmethod
    async def _load_documents(
        uow: IUnitOfWork,
        knowledge_base_id: str,
        manifest: Sequence[KnowledgeVersionDocument],
    ) -> dict[str, KnowledgeDocument]:
        documents: dict[str, KnowledgeDocument] = {}
        for entry in manifest:
            document = await uow.knowledge_base.get_document(entry.document_id)
            if document is None or document.kb_id != knowledge_base_id:
                raise ConflictError("knowledge manifest contains an invalid document owner")
            documents[document.id] = document
        return documents

    @staticmethod
    async def _validate_source_ownership(
        uow: IUnitOfWork,
        sources: Sequence[KnowledgeBuildSource],
        *,
        scope: OwnerScope,
    ) -> None:
        for source in sources:
            if source.source_type in {
                KBSourceType.WEB,
                KBSourceType.CONFLUENCE,
                KBSourceType.FEISHU,
            }:
                safe_url = validate_public_url(source.source_ref)
                if (
                    safe_url != source.source_ref
                    or source.source_identity != f"{source.source_type.value}:{safe_url}"
                ):
                    raise ValueError("URL source identity must match its validated URL")
            if source.file_id is None:
                continue
            if source.source_identity != f"file:{source.file_id}":
                raise ValueError("file source identity must match its immutable file id")
            file_info = await uow.file.get_by_id(
                source.file_id,
                scope=scope,
            )
            if file_info is None or file_info.id != source.file_id:
                # Deliberately avoid revealing whether a foreign file exists.
                raise NotFoundError("knowledge build source not found in owner scope")

    def _materialize(
        self,
        command: KnowledgeBuildCommand,
        *,
        candidate_id: str,
        base_manifest: Sequence[KnowledgeVersionDocument],
        documents: Mapping[str, KnowledgeDocument],
        revisions: Mapping[str, KnowledgeDocumentRevision],
        historical_revisions: Mapping[
            tuple[str, str],
            KnowledgeDocumentRevision,
        ],
    ) -> tuple[
        list[KnowledgeVersionDocument],
        list[KnowledgeDocument],
        list[KnowledgeDocumentRevision],
    ]:
        for entry in base_manifest:
            revision = revisions.get(entry.document_revision_id)
            if revision is None or revision.document_id != entry.document_id:
                raise ConflictError("knowledge manifest revision closure is incomplete")
        if command.operation is KnowledgeBuildOperation.ADD:
            return self._add(
                command,
                candidate_id,
                base_manifest,
                documents,
                revisions,
            )
        if command.operation is KnowledgeBuildOperation.REMOVE:
            return self._remove(
                command,
                candidate_id,
                base_manifest,
                documents,
            )
        if command.operation is KnowledgeBuildOperation.REPLACE:
            return self._replace(
                command,
                candidate_id,
                base_manifest,
                documents,
                revisions,
                historical_revisions,
            )
        return self._reindex(
            command,
            candidate_id,
            base_manifest,
            documents,
            revisions,
            historical_revisions,
        )

    @staticmethod
    def _add(
        command: KnowledgeBuildCommand,
        candidate_id: str,
        base_manifest: Sequence[KnowledgeVersionDocument],
        documents: Mapping[str, KnowledgeDocument],
        revisions: Mapping[str, KnowledgeDocumentRevision],
    ):
        identities = {
            _revision_source_identity(
                revisions[item.document_revision_id],
                documents[item.document_id],
            )
            for item in base_manifest
        }
        new_documents: list[KnowledgeDocument] = []
        new_revisions: list[KnowledgeDocumentRevision] = []
        entries = [_reuse_entry(item, candidate_id) for item in base_manifest]
        existing_ids = set(documents)
        for source in command.sources:
            if source.source_identity in identities:
                raise ValueError("duplicate source identity already exists in manifest")
            document_id = source.document_id or str(uuid.uuid4())
            if document_id in existing_ids:
                raise ValueError("duplicate logical document id already exists in manifest")
            document = _document_from_source(
                source,
                knowledge_base_id=command.knowledge_base_id,
                document_id=document_id,
            )
            revision = _revision_from_source(source, document_id=document.id)
            new_documents.append(document)
            new_revisions.append(revision)
            entries.append(
                KnowledgeVersionDocument(
                    version_id=candidate_id,
                    document_id=document.id,
                    document_revision_id=revision.id,
                    ordinal=len(entries),
                    state=DocumentRevisionState.UPLOADED,
                )
            )
            identities.add(source.source_identity)
            existing_ids.add(document_id)
        return _normalize_ordinals(entries), new_documents, new_revisions

    @staticmethod
    def _remove(
        command: KnowledgeBuildCommand,
        candidate_id: str,
        base_manifest: Sequence[KnowledgeVersionDocument],
        documents: Mapping[str, KnowledgeDocument],
    ):
        target = command.document_ids[0]
        if target not in documents:
            raise NotFoundError("document not found in knowledge base manifest")
        entries = [
            _reuse_entry(item, candidate_id) for item in base_manifest if item.document_id != target
        ]
        return _normalize_ordinals(entries), [], []

    @staticmethod
    def _replace(
        command: KnowledgeBuildCommand,
        candidate_id: str,
        base_manifest: Sequence[KnowledgeVersionDocument],
        documents: Mapping[str, KnowledgeDocument],
        revisions: Mapping[str, KnowledgeDocumentRevision],
        historical_revisions: Mapping[
            tuple[str, str],
            KnowledgeDocumentRevision,
        ],
    ):
        target = command.document_ids[0]
        if target not in documents:
            raise NotFoundError("document not found in knowledge base manifest")
        source = command.sources[0]
        identities = {
            _revision_source_identity(
                revisions[item.document_revision_id],
                documents[item.document_id],
            )
            for item in base_manifest
            if item.document_id != target
        }
        if source.source_identity in identities:
            raise ValueError("duplicate source identity already exists in manifest")
        revision = historical_revisions.get((target, source.source_digest))
        created_revision = revision is None
        if revision is None:
            revision = _revision_from_source(source, document_id=target)
        entries: list[KnowledgeVersionDocument] = []
        for item in base_manifest:
            if item.document_id == target:
                entries.append(
                    KnowledgeVersionDocument(
                        version_id=candidate_id,
                        document_id=target,
                        document_revision_id=revision.id,
                        ordinal=item.ordinal,
                        state=DocumentRevisionState.UPLOADED,
                    )
                )
            else:
                entries.append(_reuse_entry(item, candidate_id))
        return (
            _normalize_ordinals(entries),
            [],
            [revision] if created_revision else [],
        )

    @staticmethod
    def _reindex(
        command: KnowledgeBuildCommand,
        candidate_id: str,
        base_manifest: Sequence[KnowledgeVersionDocument],
        documents: Mapping[str, KnowledgeDocument],
        revisions: Mapping[str, KnowledgeDocumentRevision],
        historical_revisions: Mapping[
            tuple[str, str],
            KnowledgeDocumentRevision,
        ],
    ):
        by_document = {item.document_id: item for item in command.sources}
        unknown = set(by_document).difference(documents)
        if unknown:
            raise NotFoundError("document not found in knowledge base manifest")
        resulting_identities = {
            item.document_id: _revision_source_identity(
                revisions[item.document_revision_id],
                documents[item.document_id],
            )
            for item in base_manifest
        }
        for document_id, source in by_document.items():
            resulting_identities[document_id] = source.source_identity
        if len(set(resulting_identities.values())) != len(resulting_identities):
            raise ValueError("duplicate source identity already exists in manifest")

        entries: list[KnowledgeVersionDocument] = []
        new_revisions: list[KnowledgeDocumentRevision] = []
        for item in base_manifest:
            source = by_document.get(item.document_id)
            previous = revisions[item.document_revision_id]
            if source is None or source.source_digest == previous.source_digest:
                entries.append(_reuse_entry(item, candidate_id))
                continue
            revision = historical_revisions.get((item.document_id, source.source_digest))
            if revision is None:
                revision = _revision_from_source(
                    source,
                    document_id=item.document_id,
                )
                new_revisions.append(revision)
            entries.append(
                KnowledgeVersionDocument(
                    version_id=candidate_id,
                    document_id=item.document_id,
                    document_revision_id=revision.id,
                    ordinal=item.ordinal,
                    state=DocumentRevisionState.UPLOADED,
                )
            )
        return _normalize_ordinals(entries), [], new_revisions


def _owner_identity(resource: KnowledgeBase, scope: OwnerScope) -> str:
    if scope.type is OwnerScopeType.TEAM:
        if not scope.team_id or resource.team_id != scope.team_id:
            raise NotFoundError("knowledge base not found in owner scope")
        return f"team:{scope.team_id}"
    if resource.owner_user_id != scope.user_id or resource.team_id is not None:
        raise NotFoundError("knowledge base not found in owner scope")
    return f"user:{scope.user_id}"


def _retry_command_key(
    original: KnowledgeBaseVersion,
    *,
    owner_identity: str,
    active_version_id: str | None,
) -> str:
    payload = {
        "operation": "retry",
        "resource_kind": ResourceKind.KNOWLEDGE_BASE.value,
        "resource_id": original.knowledge_base_id,
        "source_build_id": original.build_id,
        "source_version_id": original.id,
        "source_parent_version_id": original.parent_version_id,
        "active_version_id": active_version_id,
        "owner_identity": owner_identity,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _freeze_option(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze_option(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_option(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_freeze_option(item) for item in value))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("knowledge build options must be JSON-compatible")


def _canonical_file_source_ref(file_id: str) -> str:
    return json.dumps(
        {"file_id": file_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _is_active_build_uniqueness_violation(exc: IntegrityError) -> bool:
    """Recognize only the active-build partial unique index.

    PostgreSQL drivers expose the constraint through ``diag.constraint_name``
    or ``constraint_name``. SQLite has no named partial-index diagnostics, so
    accept only its exact unique-column message for this two-column index.
    """

    pending: list[Any] = [exc.orig]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in visited:
            continue
        visited.add(id(current))
        if getattr(current, "constraint_name", None) == (
            "uq_knowledge_base_versions_building_candidate"
        ):
            return True
        diag = getattr(current, "diag", None)
        if getattr(diag, "constraint_name", None) == (
            "uq_knowledge_base_versions_building_candidate"
        ):
            return True
        message = str(current)
        if message == "uq_knowledge_base_versions_building_candidate":
            return True
        normalized = " ".join(message.lower().split())
        if normalized == "unique constraint failed: knowledge_base_versions.knowledge_base_id":
            return True
        pending.extend(
            (
                getattr(current, "__cause__", None),
                getattr(current, "__context__", None),
            )
        )
    return False


def _document_source_identity(document: KnowledgeDocument) -> str:
    return document.source_identity


def _revision_source_identity(
    revision: KnowledgeDocumentRevision,
    document: KnowledgeDocument,
) -> str:
    try:
        payload = json.loads(revision.source_ref)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        file_id = payload.get("file_id")
        if isinstance(file_id, str) and file_id.strip():
            return f"file:{file_id.strip()}"
    if revision.source_ref.strip():
        return f"{document.source_type.value}:{revision.source_ref.strip()}"
    return _document_source_identity(document)


def _document_from_source(
    source: KnowledgeBuildSource,
    *,
    knowledge_base_id: str,
    document_id: str,
) -> KnowledgeDocument:
    values: dict[str, Any] = {
        "id": document_id,
        "kb_id": knowledge_base_id,
        "title": source.title,
        "source_type": source.source_type,
        "source_ref": source.source_ref,
        "mime": source.mime,
        "file_id": source.file_id,
        "status": DocStatus.PENDING,
        "page_count": 0,
        "error": None,
        "warning": None,
    }
    return KnowledgeDocument(**values)


def _revision_from_source(
    source: KnowledgeBuildSource,
    *,
    document_id: str,
) -> KnowledgeDocumentRevision:
    return KnowledgeDocumentRevision(
        document_id=document_id,
        source_ref=source.source_ref,
        source_digest=source.source_digest,
        state=DocumentRevisionState.UPLOADED,
    )


def _reuse_entry(
    entry: KnowledgeVersionDocument,
    version_id: str,
) -> KnowledgeVersionDocument:
    return entry.model_copy(update={"version_id": version_id})


def _normalize_ordinals(
    entries: Sequence[KnowledgeVersionDocument],
) -> list[KnowledgeVersionDocument]:
    return [entry.model_copy(update={"ordinal": ordinal}) for ordinal, entry in enumerate(entries)]
