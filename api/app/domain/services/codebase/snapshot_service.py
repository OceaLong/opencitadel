#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Content-addressed immutable source snapshots for codebase builds."""
from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO, Mapping, Optional

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.services.codebase.source_validator import normalize_contained_path


SNAPSHOT_PREFIX = "codebase-snapshots/sha256"


@dataclass(frozen=True)
class MaterializedSource:
    version_id: str
    source_digest: str
    snapshot_key: str
    snapshot_bytes: bytes
    source_revision: str = ""


class CodeSourceProvenance(str, Enum):
    PUBLISHED_VERSION = "published_version"
    SESSION_WORKSPACE = "session_workspace"


@dataclass(frozen=True)
class CodeSourceReadResult:
    path: str
    content: str
    provenance: CodeSourceProvenance
    base_version_id: Optional[str] = None
    source_digest: Optional[str] = None


class VersionedCodeSource:
    """Read source files from the immutable snapshot bound to a codebase version."""

    def __init__(
        self,
        *,
        version_id: str,
        snapshot_key: str,
        source_digest: str,
        object_storage: ObjectStoragePort,
        default_max_length: Optional[int] = 10000,
    ) -> None:
        if not version_id:
            raise ValueError("version_id is required")
        if not snapshot_key:
            raise ValueError("snapshot_key is required")
        self.version_id = version_id
        self.snapshot_key = snapshot_key
        self.source_digest = source_digest
        self._object_storage = object_storage
        self._default_max_length = default_max_length

    async def read(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        *,
        max_length: Optional[int] = None,
    ) -> CodeSourceReadResult:
        relative_path = self._normalize_snapshot_path(path)
        snapshot_bytes = await self._object_storage.get_bytes(self.snapshot_key)
        content = self._read_member(snapshot_bytes, relative_path)
        content = self._slice_lines(content, start_line, end_line)
        effective_max_length = (
            self._default_max_length
            if max_length is None
            else max_length
        )
        if effective_max_length is not None:
            content = content[:effective_max_length]
        return CodeSourceReadResult(
            path=relative_path,
            content=content,
            provenance=CodeSourceProvenance.PUBLISHED_VERSION,
            base_version_id=self.version_id,
            source_digest=self.source_digest,
        )

    @staticmethod
    def _normalize_snapshot_path(path: str) -> str:
        normalized = normalize_contained_path("/source", path)
        return str(normalized.relative_to("/source"))

    @classmethod
    def _read_member(cls, snapshot_bytes: bytes, relative_path: str) -> str:
        try:
            with tarfile.open(fileobj=io.BytesIO(snapshot_bytes), mode="r:*") as archive:
                for member in archive.getmembers():
                    if member.isdir():
                        continue
                    try:
                        member_path = cls._normalize_snapshot_path(member.name)
                    except BadRequestError as exc:
                        raise BadRequestError("代码快照包含不安全路径") from exc
                    if member_path != relative_path:
                        continue
                    if not member.isfile():
                        raise BadRequestError("代码快照包含非普通文件")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise NotFoundError(f"无法读取 {relative_path}")
                    return extracted.read().decode("utf-8", errors="replace")
        except tarfile.TarError as exc:
            raise BadRequestError("代码快照格式无效") from exc
        raise NotFoundError(f"无法读取 {relative_path}")

    @staticmethod
    def _slice_lines(
        content: str,
        start_line: Optional[int],
        end_line: Optional[int],
    ) -> str:
        if start_line is None and end_line is None:
            return content
        if start_line is not None and start_line < 1:
            raise BadRequestError("起始行号必须大于等于 1")
        if end_line is not None and end_line < 1:
            raise BadRequestError("结束行号必须大于等于 1")
        if start_line is not None and end_line is not None and end_line < start_line:
            raise BadRequestError("结束行号不能小于起始行号")
        lines = content.splitlines(keepends=True)
        start_index = (start_line or 1) - 1
        end_index = end_line if end_line is not None else len(lines)
        return "".join(lines[start_index:end_index])


class CodeSnapshotService:
    async def create(
        self,
        version_id: str,
        source_tree: Mapping[str, str | bytes],
    ) -> MaterializedSource:
        digest = self._source_digest(source_tree)
        snapshot_bytes = self._stable_tgz(source_tree)
        return MaterializedSource(
            version_id=version_id,
            source_digest=digest,
            snapshot_key=f"{SNAPSHOT_PREFIX}/{digest}.tgz",
            snapshot_bytes=snapshot_bytes,
            source_revision=digest,
        )

    async def create_from_sandbox(
        self,
        version_id: str,
        sandbox,
    ) -> MaterializedSource:
        snapshot_bytes = await sandbox.create_workspace_snapshot(version_id)
        digest = hashlib.sha256(snapshot_bytes).hexdigest()
        return MaterializedSource(
            version_id=version_id,
            source_digest=digest,
            snapshot_key=f"{SNAPSHOT_PREFIX}/{digest}.tgz",
            snapshot_bytes=snapshot_bytes,
            source_revision=digest,
        )

    async def restore(
        self,
        snapshot_id: str,
        sandbox,
        snapshot_data: BinaryIO,
    ) -> None:
        await sandbox.restore_workspace_snapshot(snapshot_id, snapshot_data)

    @staticmethod
    def _source_digest(source_tree: Mapping[str, str | bytes]) -> str:
        digest = hashlib.sha256()
        for path in sorted(source_tree):
            normalize_contained_path("/source", path)
            content = source_tree[path]
            data = content.encode() if isinstance(content, str) else bytes(content)
            digest.update(path.encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(data).hexdigest().encode())
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _stable_tgz(source_tree: Mapping[str, str | bytes]) -> bytes:
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as archive:
            for path in sorted(source_tree):
                normalize_contained_path("/source", path)
                content = source_tree[path]
                data = content.encode() if isinstance(content, str) else bytes(content)
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                archive.addfile(info, io.BytesIO(data))
        return gzip.compress(tar_stream.getvalue(), mtime=0)
