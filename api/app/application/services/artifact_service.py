#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Awaitable, Callable, List, Optional

from app.domain.external.file_storage import FileStorage
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.models.artifact import Artifact, ArtifactKind, ArtifactStatus
from app.domain.models.event import ArtifactEvent
from app.domain.models.file import File
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

_VERIFY_UPLOAD_INITIAL_DELAY_SECONDS = 0.1
_VERIFY_UPLOAD_MAX_ATTEMPTS = 3
_VERIFY_UPLOAD_RETRY_DELAYS_SECONDS = (0.2, 0.5)

_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_EVENT_HANDLER_RE = re.compile(r"\s(on\w+)\s*=\s*(['\"]).*?\2", re.IGNORECASE | re.DOTALL)

SandboxFileReader = Callable[[str, str], Awaitable[Optional[str]]]


def sanitize_html_for_preview(html: str) -> str:
    """Strip active content from HTML artifacts before preview rendering."""
    cleaned = _SCRIPT_TAG_RE.sub("", html or "")
    return _EVENT_HANDLER_RE.sub("", cleaned)


def _decode_text_content(data: bytes) -> tuple[str, bool]:
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        logger.warning("交付物内容 UTF-8 不完整，已降级为 replacement 解码")
        return data.decode("utf-8", errors="replace"), True


def _encode_utf8_content(content: str) -> bytes:
    data = content.encode("utf-8")
    data.decode("utf-8")
    return data


def _artifact_file_extensions(kind: ArtifactKind) -> tuple[str, ...]:
    return (".md", ".markdown") if kind == "doc" else (".html", ".htm")


def _normalize_match_token(value: str) -> str:
    stem = os.path.splitext(value.strip())[0]
    return re.sub(r"[\s_\-]+", "", stem.lower())


def _artifact_not_found_message(artifact_id: str) -> str:
    return (
        f"交付物[{artifact_id}]不存在。"
        f"若为新交付物请将 artifact_id 留空；若为更新请使用 artifact_write 返回的 id。"
    )


def _rank_session_files_for_artifact(artifact: Artifact, files: List[File]) -> List[File]:
    allowed_ext = _artifact_file_extensions(artifact.kind)
    title_token = _normalize_match_token(artifact.title)
    ranked: list[tuple[int, File]] = []

    for file in files:
        filename = file.filename or os.path.basename(file.filepath or "")
        if not filename:
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in allowed_ext:
            continue
        name_token = _normalize_match_token(filename)
        score = 0
        if filename == artifact.title:
            score += 100
        if name_token == title_token:
            score += 80
        if title_token and title_token in name_token:
            score += 40
        if title_token and name_token in title_token:
            score += 20
        if score > 0:
            ranked.append((score, file))

    ranked.sort(key=lambda item: (-item[0], item[1].size))
    return [file for _, file in ranked]


class ArtifactService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            object_storage: ObjectStoragePort,
            file_storage: Optional[FileStorage] = None,
            sandbox_file_reader: Optional[SandboxFileReader] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._object_storage = object_storage
        self._file_storage = file_storage
        self._sandbox_file_reader = sandbox_file_reader

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
            *,
            verify_upload: bool = True,
    ) -> tuple[Artifact, ArtifactEvent]:
        data = _encode_utf8_content(content)
        async with self._uow_factory() as uow:
            if artifact_id:
                artifact = await uow.artifact.get_by_id(artifact_id)
                if not artifact or artifact.session_id != session_id:
                    raise ValueError(_artifact_not_found_message(artifact_id))
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
            logger.info(
                "写入交付物 session=%s artifact=%s version=%s byte_size=%d",
                session_id,
                artifact_id,
                version,
                len(data),
            )
            await self._object_storage.put_bytes(key, data)
            if verify_upload:
                await asyncio.sleep(_VERIFY_UPLOAD_INITIAL_DELAY_SECONDS)
                stored: bytes | None = None
                for attempt in range(_VERIFY_UPLOAD_MAX_ATTEMPTS):
                    stored = await self._object_storage.get_bytes(key)
                    if stored == data:
                        break
                    if attempt < _VERIFY_UPLOAD_MAX_ATTEMPTS - 1:
                        delay = _VERIFY_UPLOAD_RETRY_DELAYS_SECONDS[attempt]
                        logger.warning(
                            "交付物上传校验不一致，准备重试 artifact=%s attempt=%s/%s expected=%d got=%d",
                            artifact_id,
                            attempt + 1,
                            _VERIFY_UPLOAD_MAX_ATTEMPTS,
                            len(data),
                            len(stored),
                        )
                        await asyncio.sleep(delay)
                if stored != data:
                    raise ValueError(
                        f"交付物上传校验失败: artifact={artifact_id} expected={len(data)} got={len(stored)}"
                    )

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
                raise ValueError(_artifact_not_found_message(artifact_id))
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

    async def _read_session_file_text(self, file: File) -> Optional[str]:
        if not self._file_storage:
            return None
        try:
            stream, _ = await self._file_storage.download_file(file.id)
            data = stream.read()
            text, incomplete = _decode_text_content(data)
            if incomplete:
                return None
            return text
        except Exception as exc:
            logger.warning("读取会话附件失败 file_id=%s: %s", file.id, exc)
            return None

    async def _try_recover_content(
            self,
            artifact: Artifact,
            *,
            scope: Optional[OwnerScope] = None,
    ) -> Optional[tuple[str, str]]:
        async with self._uow_factory() as uow:
            files = await uow.session.get_files(artifact.session_id, scope=scope)
        if files:
            for file in _rank_session_files_for_artifact(artifact, files):
                text = await self._read_session_file_text(file)
                if text:
                    return text, f"session_file:{file.filepath or file.filename}"

        if self._sandbox_file_reader:
            for file in files or []:
                if not file.filepath:
                    continue
                try:
                    text = await self._sandbox_file_reader(artifact.session_id, file.filepath)
                except Exception as exc:
                    logger.warning(
                        "沙箱恢复交付物失败 session=%s path=%s: %s",
                        artifact.session_id,
                        file.filepath,
                        exc,
                    )
                    continue
                if text:
                    return text, f"sandbox:{file.filepath}"

        return None

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
                raise ValueError(_artifact_not_found_message(artifact_id))
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
            text, _ = _decode_text_content(data)
            return sanitize_html_for_preview(text).encode("utf-8")
        return data

    async def get_content_text(
            self,
            artifact_id: str,
            version_index: Optional[int] = None,
            *,
            scope: Optional[OwnerScope] = None,
            sanitize_html: bool = True,
            auto_repair: bool = True,
    ) -> tuple[str, bool]:
        data = await self.get_content(
            artifact_id,
            version_index=version_index,
            scope=scope,
            sanitize_html=False,
        )
        text, incomplete = _decode_text_content(data)
        if incomplete and auto_repair:
            artifact = await self.get_by_id(artifact_id, scope=scope)
            if artifact:
                recovered = await self._try_recover_content(artifact, scope=scope)
                if recovered:
                    recovered_text, source = recovered
                    logger.info(
                        "交付物[%s] 从不完整存储恢复，来源=%s",
                        artifact_id,
                        source,
                    )
                    await self.write_content(
                        session_id=artifact.session_id,
                        artifact_id=artifact.id,
                        kind=artifact.kind,
                        title=artifact.title,
                        content=recovered_text,
                    )
                    text = recovered_text
                    incomplete = False
        if sanitize_html:
            artifact = await self.get_by_id(artifact_id, scope=scope)
            if artifact and artifact.kind == "web":
                text = sanitize_html_for_preview(text)
        return text, incomplete

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
                raise ValueError(_artifact_not_found_message(artifact_id))
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
