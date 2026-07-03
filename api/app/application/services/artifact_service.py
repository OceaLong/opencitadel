#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from app.domain.external.object_storage import ObjectStoragePort
from app.domain.models.artifact import Artifact, ArtifactStatus
from app.domain.models.event import ArtifactEvent
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_EVENT_HANDLER_RE = re.compile(r"\s(on\w+)\s*=\s*(['\"]).*?\2", re.IGNORECASE | re.DOTALL)


def sanitize_html_for_preview(html: str) -> str:
    """Strip active content from HTML artifacts before preview rendering."""
    cleaned = _SCRIPT_TAG_RE.sub("", html or "")
    return _EVENT_HANDLER_RE.sub("", cleaned)


class ArtifactService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            object_storage: ObjectStoragePort,
    ) -> None:
        self._uow_factory = uow_factory
        self._object_storage = object_storage

    def _storage_key(self, session_id: str, artifact_id: str, version: int, kind: str) -> str:
        ext = "md" if kind == "doc" else "html"
        return f"artifacts/{session_id}/{artifact_id}/v{version}.{ext}"

    async def write_content(
            self,
            session_id: str,
            artifact_id: Optional[str],
            kind: str,
            title: str,
            content: str,
    ) -> tuple[Artifact, ArtifactEvent]:
        async with self._uow_factory() as uow:
            if artifact_id:
                artifact = await uow.artifact.get_by_id(artifact_id)
                if not artifact or artifact.session_id != session_id:
                    raise ValueError(f"交付物[{artifact_id}]不存在")
                version = len(artifact.version_refs) + 1
                status: ArtifactStatus = "updated"
            else:
                artifact_id = str(uuid.uuid4())
                artifact = Artifact(
                    id=artifact_id,
                    session_id=session_id,
                    kind=kind,  # type: ignore[arg-type]
                    title=title,
                )
                version = 1
                status = "draft"

            key = self._storage_key(session_id, artifact_id, version, kind)
            await self._object_storage.put_bytes(key, content.encode("utf-8"))

            artifact.title = title or artifact.title
            artifact.storage_ref = key
            artifact.version_refs = list(artifact.version_refs) + [key]
            artifact.status = status
            artifact.updated_at = datetime.now()
            await uow.artifact.save(artifact)
            await uow.commit()

        event = ArtifactEvent(
            artifact_id=artifact.id,
            kind=artifact.kind,
            title=artifact.title,
            status=status,
            storage_ref=key,
            version=version,
        )
        return artifact, event

    async def finalize(self, session_id: str, artifact_id: str) -> tuple[Artifact, ArtifactEvent]:
        async with self._uow_factory() as uow:
            artifact = await uow.artifact.get_by_id(artifact_id)
            if not artifact or artifact.session_id != session_id:
                raise ValueError(f"交付物[{artifact_id}]不存在")
            artifact.status = "final"
            artifact.updated_at = datetime.now()
            await uow.artifact.save(artifact)
            await uow.commit()

        event = ArtifactEvent(
            artifact_id=artifact.id,
            kind=artifact.kind,
            title=artifact.title,
            status="final",
            storage_ref=artifact.storage_ref,
            version=len(artifact.version_refs),
        )
        return artifact, event

    async def _assert_session_scope(self, session_id: str, scope: OwnerScope) -> None:
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id, scope=scope)
            if not session:
                raise PermissionError(f"无权访问会话[{session_id}]")

    async def get_content(
            self,
            artifact_id: str,
            version_index: Optional[int] = None,
            *,
            scope: Optional[OwnerScope] = None,
            sanitize_html: bool = True,
    ) -> bytes:
        async with self._uow_factory() as uow:
            artifact = await uow.artifact.get_by_id(artifact_id)
            if not artifact:
                raise ValueError(f"交付物[{artifact_id}]不存在")
            if scope is not None:
                session = await uow.session.get_metadata(artifact.session_id, scope=scope)
                if not session:
                    raise PermissionError(f"无权访问交付物[{artifact_id}]")
            if version_index is not None:
                if version_index < 1 or version_index > len(artifact.version_refs):
                    raise ValueError("版本不存在")
                key = artifact.version_refs[version_index - 1]
            else:
                key = artifact.storage_ref
            kind = artifact.kind
        data = await self._object_storage.get_bytes(key)
        if sanitize_html and kind == "web":
            return sanitize_html_for_preview(data.decode("utf-8")).encode("utf-8")
        return data

    async def list_by_session(self, session_id: str, scope: Optional[OwnerScope] = None) -> List[Artifact]:
        if scope is not None:
            await self._assert_session_scope(session_id, scope)
        async with self._uow_factory() as uow:
            return await uow.artifact.list_by_session(session_id)

    async def get_by_id(self, artifact_id: str, scope: Optional[OwnerScope] = None) -> Optional[Artifact]:
        async with self._uow_factory() as uow:
            artifact = await uow.artifact.get_by_id(artifact_id)
            if not artifact:
                return None
            if scope is not None:
                session = await uow.session.get_metadata(artifact.session_id, scope=scope)
                if not session:
                    return None
            return artifact

    async def create_share_link(
            self,
            artifact_id: str,
            ttl_hours: int = 168,
            *,
            scope: Optional[OwnerScope] = None,
    ) -> str:
        token = secrets.token_urlsafe(24)
        expires = datetime.now() + timedelta(hours=ttl_hours)
        async with self._uow_factory() as uow:
            artifact = await uow.artifact.get_by_id(artifact_id)
            if not artifact:
                raise ValueError(f"交付物[{artifact_id}]不存在")
            if scope is not None:
                session = await uow.session.get_metadata(artifact.session_id, scope=scope)
                if not session:
                    raise PermissionError(f"无权分享交付物[{artifact_id}]")
            artifact.share_token = token
            artifact.share_expires_at = expires
            await uow.artifact.save(artifact)
            await uow.commit()
        return token

    async def get_by_share_token(self, token: str) -> Optional[Artifact]:
        async with self._uow_factory() as uow:
            artifact = await uow.artifact.get_by_share_token(token)
            if not artifact:
                return None
            if artifact.share_expires_at and artifact.share_expires_at < datetime.now():
                return None
            return artifact
