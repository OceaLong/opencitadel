"""Files, Artifacts, and immutable knowledge version operations."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import ConflictError, NotFoundError
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.kernel.application.command_service import CommandService
from app.kernel.application.ports import KernelAuthorization
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.types import OwnerScopeRef, Workflow
from app.kernel.infrastructure.postgres.session_auth import bind_context

from .models import (
    ArtifactORM,
    FileORM,
    KnowledgeBaseORM,
    KnowledgeChunkORM,
    KnowledgeDocumentORM,
    KnowledgeVersionORM,
)


def _owner(scope: OwnerScope | OwnerScopeRef) -> dict[str, str | None]:
    team_id = scope.team_id
    user_id = getattr(scope, "user_id", None) or getattr(scope, "owner_user_id", None)
    return {
        "owner_user_id": None if team_id else user_id,
        "team_id": team_id,
    }


def _kb_view(row: KnowledgeBaseORM) -> dict[str, object]:
    return {
        "id": str(row.id),
        "name": row.name,
        "activeVersionId": str(row.active_version_id) if row.active_version_id else None,
        "archivedAt": row.archived_at.isoformat() if row.archived_at else None,
        "purgeAfter": row.purge_after.isoformat() if row.purge_after else None,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
        "teamId": row.team_id,
    }


class PostgresKnowledgeService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        storage: Any,
        commands: CommandService,
        retention_days: int,
        quota: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._commands = commands
        self._retention_days = retention_days
        self._quota = quota

    async def upload_file(
        self,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        scope: OwnerScope,
    ) -> dict[str, object]:
        file_id = uuid4()
        digest = hashlib.sha256(content).hexdigest()
        storage_ref = f"files/{file_id}/{digest}"
        await self._storage.put_bytes(storage_ref, content)
        now = datetime.now(UTC)
        try:
            async with self._session_factory() as session, session.begin():
                await bind_context(session)
                if self._quota is not None:
                    owner_scope = (
                        OwnerScopeRef.team(scope.team_id)
                        if scope.team_id
                        else OwnerScopeRef.personal(scope.user_id)
                    )
                    await self._quota.validate_storage(
                        session,
                        owner_scope,
                        actor_user_id=scope.user_id,
                        incoming_bytes=len(content),
                    )
                    await bind_context(session)
                session.add(
                    FileORM(
                        id=file_id,
                        created_by_user_id=scope.user_id,
                        filename=filename,
                        mime_type=mime_type,
                        size=len(content),
                        digest=digest,
                        storage_ref=storage_ref,
                        created_at=now,
                        **_owner(scope),
                    )
                )
        except BaseException:
            await self._storage.delete_bytes(storage_ref)
            raise
        return await self.get_file(file_id)

    async def list_files(self) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (
                await session.scalars(select(FileORM).order_by(FileORM.created_at.desc()))
            ).all()
        return [self._file_view(row) for row in rows]

    async def get_file(self, file_id: UUID) -> dict[str, object]:
        async with self._session_factory() as session:
            await bind_context(session)
            row = await session.get(FileORM, file_id)
        if row is None:
            raise NotFoundError("File not found")
        return self._file_view(row)

    async def download_file(self, file_id: UUID) -> tuple[bytes, dict[str, object]]:
        async with self._session_factory() as session:
            await bind_context(session)
            row = await session.get(FileORM, file_id)
        if row is None:
            raise NotFoundError("File not found")
        return await self._storage.get_bytes(row.storage_ref), self._file_view(row)

    async def delete_file(self, file_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            row = await session.get(FileORM, file_id)
            if row is None:
                raise NotFoundError("File not found")
            storage_ref = row.storage_ref
            await session.delete(row)
        await self._storage.delete_bytes(storage_ref)

    @staticmethod
    def _file_view(row: FileORM) -> dict[str, object]:
        return {
            "id": str(row.id),
            "filename": row.filename,
            "mimeType": row.mime_type,
            "size": row.size,
            "digest": row.digest,
            "createdAt": row.created_at.isoformat(),
            "teamId": row.team_id,
        }

    async def list_artifacts(self, run_id: UUID | None = None) -> list[dict[str, object]]:
        statement = select(ArtifactORM)
        if run_id is not None:
            statement = statement.where(ArtifactORM.run_id == run_id)
        statement = statement.order_by(ArtifactORM.created_at.desc())
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (await session.scalars(statement)).all()
        return [self._artifact_view(row) for row in rows]

    async def get_artifact(self, artifact_id: UUID) -> dict[str, object]:
        async with self._session_factory() as session:
            await bind_context(session)
            row = await session.get(ArtifactORM, artifact_id)
        if row is None:
            raise NotFoundError("Artifact not found")
        return self._artifact_view(row)

    async def artifact_content(self, artifact_id: UUID) -> tuple[bytes, str]:
        async with self._session_factory() as session:
            await bind_context(session)
            row = await session.get(ArtifactORM, artifact_id)
        if row is None:
            raise NotFoundError("Artifact not found")
        return await self._storage.get_bytes(row.storage_ref), row.media_type

    @staticmethod
    def _artifact_view(row: ArtifactORM) -> dict[str, object]:
        return {
            "id": str(row.id),
            "runId": str(row.run_id),
            "kind": row.kind,
            "title": row.title,
            "digest": row.digest,
            "mediaType": row.media_type,
            "versionRefs": row.version_refs,
            "finalizedAt": row.finalized_at.isoformat() if row.finalized_at else None,
            "createdAt": row.created_at.isoformat(),
        }

    async def create_kb(self, name: str, *, scope: OwnerScope) -> dict[str, object]:
        now = datetime.now(UTC)
        row = KnowledgeBaseORM(
            id=uuid4(),
            name=name,
            active_version_id=None,
            archived_at=None,
            purge_after=None,
            created_at=now,
            updated_at=now,
            **_owner(scope),
        )
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            session.add(row)
        return _kb_view(row)

    async def list_kbs(self, *, include_archived: bool = False) -> list[dict[str, object]]:
        statement = select(KnowledgeBaseORM)
        if not include_archived:
            statement = statement.where(KnowledgeBaseORM.archived_at.is_(None))
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (
                await session.scalars(statement.order_by(KnowledgeBaseORM.updated_at.desc()))
            ).all()
        return [_kb_view(row) for row in rows]

    async def get_kb(self, kb_id: UUID) -> dict[str, object]:
        async with self._session_factory() as session:
            await bind_context(session)
            row = await session.get(KnowledgeBaseORM, kb_id)
        if row is None:
            raise NotFoundError("Knowledge base not found")
        return _kb_view(row)

    async def list_versions(self, kb_id: UUID) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (
                await session.scalars(
                    select(KnowledgeVersionORM)
                    .where(KnowledgeVersionORM.knowledge_base_id == kb_id)
                    .order_by(KnowledgeVersionORM.created_at.desc())
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "knowledgeBaseId": str(row.knowledge_base_id),
                "buildRunId": str(row.build_run_id),
                "state": row.state,
                "manifestDigest": row.manifest_digest,
                "documentCount": row.document_count,
                "chunkCount": row.chunk_count,
                "createdAt": row.created_at.isoformat(),
                "publishedAt": row.published_at.isoformat() if row.published_at else None,
            }
            for row in rows
        ]

    async def start_build(
        self,
        kb_id: UUID,
        file_ids: list[UUID],
        *,
        scope: OwnerScope,
        actor_user_id: str,
    ) -> dict[str, object]:
        run_id = uuid4()
        version_id = uuid4()
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            kb = await session.get(KnowledgeBaseORM, kb_id)
            if kb is None or kb.archived_at is not None:
                raise NotFoundError("Knowledge base not found")
            visible_file_ids = set(
                await session.scalars(select(FileORM.id).where(FileORM.id.in_(file_ids)))
            )
            if visible_file_ids != set(file_ids):
                raise NotFoundError("One or more files are not visible in this workspace")
            session.add(
                KnowledgeVersionORM(
                    id=version_id,
                    knowledge_base_id=kb_id,
                    parent_version_id=kb.active_version_id,
                    build_run_id=run_id,
                    state="candidate",
                    manifest_digest=None,
                    document_count=0,
                    chunk_count=0,
                    created_at=now,
                    published_at=None,
                )
            )
            active_version_id = kb.active_version_id
        owner_scope = (
            OwnerScopeRef.team(scope.team_id)
            if scope.team_id
            else OwnerScopeRef.personal(scope.user_id)
        )
        try:
            result = await self._commands.submit(
                CommandEnvelope(
                    command_id=uuid4(),
                    run_id=run_id,
                    workflow=Workflow.KNOWLEDGE_INGEST,
                    type="StartKnowledgeIngest",
                    payload={
                        "knowledge_base_id": str(kb_id),
                        "candidate_version_id": str(version_id),
                        "active_version_id": (
                            str(active_version_id) if active_version_id else None
                        ),
                        "document_ids": [str(value) for value in file_ids],
                    },
                    expected_stream_version=0,
                    owner_scope=owner_scope,
                    actor_user_id=actor_user_id,
                    request_id=str(uuid4()),
                    submitted_at=now,
                ),
                KernelAuthorization.for_user(actor_user_id, owner_scope),
            )
        except BaseException:
            await self._delete_candidate(version_id)
            raise
        if result.error_code:
            await self._delete_candidate(version_id)
            raise ConflictError(result.error_message or result.error_code)
        return {
            "runId": str(run_id),
            "candidateVersionId": str(version_id),
            "streamVersion": result.stream_version,
        }

    async def _delete_candidate(self, version_id: UUID) -> None:
        async with self._system_session("knowledge-candidate-cleanup") as session:
            version = await session.get(KnowledgeVersionORM, version_id)
            if version is not None and version.state == "candidate":
                await session.delete(version)

    async def disposition(self, kb_id: UUID, *, action: str) -> dict[str, object]:
        value = await self.get_kb(kb_id)
        bound = {
            "action": action,
            "resourceId": str(kb_id),
            "updatedAt": value["updatedAt"],
            "confirmation": f"{action.upper()} KNOWLEDGE {kb_id}",
            "recoverable": action == "archive",
        }
        digest = hashlib.sha256(
            json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            **bound,
            "planHash": digest,
            "purgeAfter": (datetime.now(UTC) + timedelta(days=self._retention_days)).isoformat()
            if action == "archive"
            else datetime.now(UTC).isoformat(),
        }

    async def apply_disposition(
        self,
        kb_id: UUID,
        *,
        action: str,
        plan_hash: str,
        confirmation: str,
    ) -> dict[str, object]:
        plan = await self.disposition(kb_id, action=action)
        if not hmac.compare_digest(str(plan["planHash"]), plan_hash) or not hmac.compare_digest(
            str(plan["confirmation"]), confirmation
        ):
            raise ConflictError("stale knowledge disposition plan")
        now = datetime.now(UTC)
        storage_refs: list[str] = []
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            kb = await session.get(KnowledgeBaseORM, kb_id)
            if kb is None:
                raise NotFoundError("Knowledge base not found")
            if action == "archive":
                kb.archived_at = now
                kb.purge_after = now + timedelta(days=self._retention_days)
                kb.updated_at = now
            elif action == "restore":
                kb.archived_at = None
                kb.purge_after = None
                kb.updated_at = now
            elif action == "purge":
                storage_refs = list(
                    await session.scalars(
                        select(KnowledgeDocumentORM.storage_ref).where(
                            KnowledgeDocumentORM.knowledge_base_id == kb_id
                        )
                    )
                )
                await session.delete(kb)
            else:
                raise ValueError("unsupported knowledge disposition")
        for ref in storage_refs:
            await self._storage.delete_bytes(ref)
        return {"action": action, "resourceId": str(kb_id)}

    async def retrieve(
        self,
        query: str,
        *,
        version_ids: tuple[str, ...],
    ) -> list[dict[str, object]]:
        ids = [UUID(value) for value in version_ids]
        async with self._system_session("knowledge-retrieval") as session:
            rows = (
                await session.scalars(
                    select(KnowledgeChunkORM)
                    .where(
                        KnowledgeChunkORM.version_id.in_(ids),
                        KnowledgeChunkORM.content.ilike(f"%{query}%"),
                    )
                    .limit(12)
                )
            ).all()
        return [
            {
                "text": row.content,
                "citation": {
                    "documentId": str(row.document_id),
                    "versionId": str(row.version_id),
                    "ordinal": row.ordinal,
                },
                "score": 1.0,
            }
            for row in rows
        ]

    async def advance_build(
        self,
        request: dict[str, object],
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        del idempotency_key
        stage = str(request["stage"])
        kb_id = UUID(str(request["knowledge_base_id"]))
        version_id = UUID(str(request["candidate_version_id"]))
        file_ids = [UUID(str(value)) for value in request.get("document_ids", [])]
        if stage == "parse":
            await self._parse_files(kb_id, version_id, file_ids)
        elif stage == "chunk":
            await self._chunk_documents(kb_id, version_id)
        elif stage not in {"embed", "graph", "manifest"}:
            raise ValueError("unknown knowledge build stage")
        if stage != "manifest":
            return {"stage": stage, "version_id": str(version_id)}
        async with self._system_session("knowledge-manifest") as session:
            chunks = list(
                await session.scalars(
                    select(KnowledgeChunkORM.content)
                    .where(KnowledgeChunkORM.version_id == version_id)
                    .order_by(KnowledgeChunkORM.ordinal)
                )
            )
        digest = hashlib.sha256("\n".join(chunks).encode()).hexdigest()
        return {
            "stage": stage,
            "version_id": str(version_id),
            "manifest_digest": digest,
            "metrics": {"chunks": len(chunks)},
        }

    async def _parse_files(
        self,
        kb_id: UUID,
        version_id: UUID,
        file_ids: list[UUID],
    ) -> None:
        async with self._system_session("knowledge-parse") as session:
            existing = await session.scalar(
                select(func.count())
                .select_from(KnowledgeDocumentORM)
                .where(KnowledgeDocumentORM.version_id == version_id)
            )
            if existing:
                return
            files = (await session.scalars(select(FileORM).where(FileORM.id.in_(file_ids)))).all()
            for item in files:
                session.add(
                    KnowledgeDocumentORM(
                        id=uuid4(),
                        knowledge_base_id=kb_id,
                        version_id=version_id,
                        title=item.filename,
                        source_type="file",
                        source_digest=item.digest,
                        storage_ref=item.storage_ref,
                        metadata_json={"mimeType": item.mime_type},
                        created_at=datetime.now(UTC),
                    )
                )
            version = await session.get(KnowledgeVersionORM, version_id)
            if version is not None:
                version.document_count = len(files)

    async def _chunk_documents(self, kb_id: UUID, version_id: UUID) -> None:
        async with self._system_session("knowledge-chunk") as session:
            existing = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkORM)
                .where(KnowledgeChunkORM.version_id == version_id)
            )
            if existing:
                return
            documents = [
                (row.id, row.storage_ref)
                for row in (
                    await session.scalars(
                        select(KnowledgeDocumentORM).where(
                            KnowledgeDocumentORM.version_id == version_id
                        )
                    )
                ).all()
            ]
        # Object storage is an external Effect boundary. Never hold database
        # locks or a transaction open while waiting for it.
        contents = [
            (document_id, await self._storage.get_bytes(storage_ref))
            for document_id, storage_ref in documents
        ]
        async with self._system_session("knowledge-chunk-write") as session:
            existing = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkORM)
                .where(KnowledgeChunkORM.version_id == version_id)
            )
            if existing:
                return
            ordinal = 0
            for document_id, raw in contents:
                text = raw.decode("utf-8", errors="replace")
                for start in range(0, len(text), 1200):
                    content = text[start : start + 1200]
                    if not content.strip():
                        continue
                    session.add(
                        KnowledgeChunkORM(
                            id=uuid4(),
                            knowledge_base_id=kb_id,
                            version_id=version_id,
                            document_id=document_id,
                            ordinal=ordinal,
                            content=content,
                            content_tsv=func.to_tsvector("simple", content),
                            embedding=None,
                            metadata_json={},
                        )
                    )
                    ordinal += 1
            version = await session.get(KnowledgeVersionORM, version_id)
            if version is not None:
                version.chunk_count = ordinal

    def _system_session(self, actor: str):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def open_session():
            async with self._session_factory() as session, session.begin():
                await bind_context(session, AuthorizationContext.system(actor))
                yield session

        return open_session()


__all__ = ["PostgresKnowledgeService"]
