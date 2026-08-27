from dataclasses import dataclass
from typing import BinaryIO, Protocol

from app.domain.models.file import File


@dataclass
class FileUploadPayload:
    file: BinaryIO
    filename: str
    size: int | None = None
    content_type: str = ""
    owner_user_id: str | None = None
    team_id: str | None = None


class FileStorage(Protocol):
    """文件存储桶协议"""

    async def upload_file(self, payload: FileUploadPayload) -> File:
        """根据传递的文件源上传文件后返回文件信息"""
        ...

    async def download_file(self, file_id: str) -> tuple[BinaryIO, File]:
        """根据传递的文件id下载文件，并返回文件源+文件信息"""
        ...

    async def presigned_get_url(
        self,
        key: str,
        *,
        expires_seconds: int,
    ) -> str | None:
        """Return an externally reachable temporary URL for a stored object."""
        ...
