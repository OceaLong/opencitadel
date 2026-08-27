"""Codebase source acquisition and path safety validation."""

from __future__ import annotations

import ipaddress
import posixpath
import socket
import stat
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import urlparse

from app.domain.errors import BadRequestError
from app.domain.models.codebase import CodebaseSourceType
from app.domain.models.scope import OwnerScope

Resolver = Callable[[str, int], Iterable[str]]


@dataclass(frozen=True)
class ValidatedCodebaseSource:
    source_type: CodebaseSourceType
    source_ref: str
    file_id: str | None = None
    file_ids: tuple[str, ...] = ()
    git_url: str | None = None


def normalize_contained_path(root: str, requested: str) -> PurePosixPath:
    """Return a root-contained POSIX path or raise.

    The check is deliberately lexical. It rejects absolute requests and any
    parent traversal before constructing the sandbox path.
    """
    root_path = PurePosixPath(root)
    requested_path = PurePosixPath(requested or "")
    if not str(requested_path) or requested_path.is_absolute():
        raise BadRequestError("代码路径必须是相对路径")
    if any(part in {"", ".", ".."} for part in requested_path.parts):
        raise BadRequestError("代码路径不能包含目录穿越")

    candidate = root_path / requested_path
    try:
        common = posixpath.commonpath([str(root_path), str(candidate)])
    except ValueError as exc:
        raise BadRequestError("代码路径不能逃逸工作区") from exc
    if common != str(root_path):
        raise BadRequestError("代码路径不能逃逸工作区")
    return candidate


class CodebaseSourceValidator:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        allow_non_default_git_ports: bool = False,
        max_zip_entries: int = 10_000,
        max_zip_uncompressed_bytes: int = 256 * 1024 * 1024,
        max_zip_compression_ratio: float = 100.0,
    ) -> None:
        self._resolver = resolver or self._resolve_host
        self._allow_non_default_git_ports = allow_non_default_git_ports
        self._max_zip_entries = max_zip_entries
        self._max_zip_uncompressed_bytes = max_zip_uncompressed_bytes
        self._max_zip_compression_ratio = max_zip_compression_ratio

    def validate_source_shape(
        self,
        source_type: CodebaseSourceType,
        *,
        file_id: str | None = None,
        git_url: str | None = None,
        file_ids: list[str] | None = None,
    ) -> ValidatedCodebaseSource:
        source_type = CodebaseSourceType(source_type)
        if source_type is CodebaseSourceType.ZIP:
            if not file_id:
                raise BadRequestError("zip 代码库需要 file_id")
            return ValidatedCodebaseSource(
                source_type=source_type,
                source_ref=f'{{"file_id": "{file_id}"}}',
                file_id=file_id,
            )
        if source_type is CodebaseSourceType.GIT:
            if not git_url:
                raise BadRequestError("git 代码库需要 git_url")
            normalized_url = self.validate_git_url(git_url)
            return ValidatedCodebaseSource(
                source_type=source_type,
                source_ref=normalized_url,
                git_url=normalized_url,
            )

        deduped = tuple(dict.fromkeys(file_ids or []))
        if not deduped:
            raise BadRequestError("files 代码库需要至少一个 file_id")
        encoded = ", ".join(f'"{item}"' for item in deduped)
        return ValidatedCodebaseSource(
            source_type=source_type,
            source_ref=f'{{"file_ids": [{encoded}]}}',
            file_ids=deduped,
        )

    async def validate_create(
        self,
        source_type: CodebaseSourceType,
        *,
        file_id: str | None = None,
        git_url: str | None = None,
        file_ids: list[str] | None = None,
        uow,
        file_storage,
        scope: OwnerScope | None = None,
    ) -> ValidatedCodebaseSource:
        validated = self.validate_source_shape(
            source_type,
            file_id=file_id,
            git_url=git_url,
            file_ids=file_ids,
        )
        if validated.source_type is CodebaseSourceType.GIT:
            return validated
        if validated.source_type is CodebaseSourceType.FILES:
            owned = await uow.file.list_by_ids(list(validated.file_ids), scope=scope)
            if {file.id for file in owned} != set(validated.file_ids):
                raise BadRequestError("文件不存在或无权使用")
            return validated

        file_info = await uow.file.get_by_id(validated.file_id, scope=scope)
        if file_info is None:
            raise BadRequestError("zip 文件不存在或无权使用")
        try:
            stream, _storage_info = await file_storage.download_file(validated.file_id)
            payload = stream.read()
        except (OSError, RuntimeError, ValueError) as exc:
            raise BadRequestError("zip 文件不可下载") from exc
        self.validate_zip_bytes(payload)
        return validated

    def validate_git_url(self, url: str) -> str:
        if any(char in url for char in ("\r", "\n", "\t", " ", ";", "|", "&", "`", "$", "<", ">")):
            raise BadRequestError("Git URL 包含不安全字符")
        parsed = urlparse(url or "")
        if parsed.scheme != "https":
            raise BadRequestError("Git URL 只允许 https")
        if not parsed.hostname:
            raise BadRequestError("Git URL 缺少主机名")
        if parsed.username or parsed.password:
            raise BadRequestError("Git URL 不能包含凭据")
        port = parsed.port or 443
        if not self._allow_non_default_git_ports and parsed.port is not None and parsed.port != 443:
            raise BadRequestError("Git URL 不允许非默认端口")
        for address in self._addresses_for_host(parsed.hostname, port):
            self._reject_unsafe_address(address)
        return url

    def validate_zip_bytes(self, payload: bytes) -> None:
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                infos = archive.infolist()
                if len(infos) > self._max_zip_entries:
                    raise BadRequestError("zip 条目数量超过限制")
                total_size = 0
                for info in infos:
                    self._validate_zip_member(info)
                    if info.is_dir():
                        continue
                    total_size += int(info.file_size)
                    if total_size > self._max_zip_uncompressed_bytes:
                        raise BadRequestError("zip 解压后体积超过限制")
                    if info.file_size > 0 and info.compress_size == 0:
                        raise BadRequestError("zip 压缩比异常")
                    if info.compress_size > 0:
                        ratio = info.file_size / info.compress_size
                        if ratio > self._max_zip_compression_ratio:
                            raise BadRequestError("zip 压缩比超过限制")
        except BadRequestError:
            raise
        except zipfile.BadZipFile as exc:
            raise BadRequestError("zip 文件格式无效") from exc

    def _validate_zip_member(self, info: zipfile.ZipInfo) -> None:
        normalize_contained_path("/workspace/codebase", info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise BadRequestError("zip 不允许符号链接")

    def _addresses_for_host(self, host: str, port: int) -> list[str]:
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            return list(self._resolver(host, port))

    @staticmethod
    def _resolve_host(host: str, port: int) -> list[str]:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return sorted({item[4][0] for item in infos})

    @staticmethod
    def _reject_unsafe_address(address: str) -> None:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise BadRequestError("Git URL 解析到非公网地址")
