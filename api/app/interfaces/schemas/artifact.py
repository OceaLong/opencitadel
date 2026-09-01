from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ArtifactResponse(BaseModel):
    id: str
    session_id: str
    kind: Literal["doc", "web"]
    title: str
    storage_ref: str
    version_refs: list[str]
    status: Literal["draft", "updated", "final"]
    created_at: datetime
    updated_at: datetime
    # 分享状态(脱敏):is_shared 表示当前是否存在有效(未过期)的公开分享链接;
    # share_expires_at 暴露到期时间供前端常驻展示;share_token_preview 仅回传令牌后 4 位,
    # 用于让用户辨认是哪条链接,绝不回传完整 share_token(它是未鉴权访问凭据)。
    is_shared: bool = False
    share_expires_at: datetime | None = None
    share_token_preview: str | None = None


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactResponse]


class ArtifactShareResponse(BaseModel):
    # 完整 share_token 仅在创建/轮换这一刻返回一次,供前端立即复制分享链接。
    share_token: str
    share_url: str
    share_expires_at: datetime | None = None


class ArtifactContentResponse(BaseModel):
    content: str
    content_type: str
    incomplete: bool = False
