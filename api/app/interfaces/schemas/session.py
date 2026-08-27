from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.models.codebase import SessionMode
from app.domain.models.file import File
from app.domain.models.resource_bindings import ResourceBindingProjection
from app.domain.models.session import SessionStatus
from app.domain.utils.time_utils import utc_now
from app.interfaces.schemas.inference import InferenceModelResponse
from app.interfaces.schemas.skill import SkillSummaryResponse


class CreateSessionRequest(BaseModel):
    """创建会话请求"""

    title: str | None = None
    model_id: str | None = None
    skill_id: str | None = None
    thinking_enabled: bool | None = None
    codebase_id: str | None = None
    codebase_version_id: str | None = None
    knowledge_base_id: str | None = None
    knowledge_base_version_id: str | None = None
    mode: SessionMode | None = None
    operator_scope: str | None = Field(
        default=None,
        description="Web Operator 目标系统归属: owned | third_party_saas",
    )
    operator_domains: list[str] = Field(
        default_factory=list,
        description="Web Operator 域名白名单",
    )


class CreateSessionResponse(BaseModel):
    """创建会话响应结构"""

    session_id: str  # 会话id


class UpgradeResourceBindingRequest(BaseModel):
    target_version_id: str


class ResourceBindingResponse(BaseModel):
    binding_id: str
    resource_kind: str
    resource_id: str
    version_id: str
    is_current: bool
    supersedes_binding_id: str | None = None


class UpgradeResourceBindingResponse(BaseModel):
    old_binding_id: str
    new_binding_id: str
    current_version_id: str


class ListSessionItem(BaseModel):
    """会话列表条目基础信息"""

    session_id: str = ""
    title: str = ""
    latest_message: str = ""
    latest_message_at: datetime | None = Field(default_factory=utc_now)
    status: SessionStatus = SessionStatus.PENDING
    unread_message_count: int = 0
    mode: SessionMode | None = None
    resource_bindings: list[ResourceBindingProjection] = Field(default_factory=list)


class ListSessionResponse(BaseModel):
    """获取会话列表基础信息响应结构"""

    sessions: list[ListSessionItem]


class ChatRequest(BaseModel):
    """聊天请求结构"""

    message: str | None = None  # 人类消息
    request_id: UUID | None = None
    attachments: list[str] = Field(default_factory=list, max_length=10)
    event_id: str | None = None  # 最新事件id
    timestamp: int | None = None  # 当前时间戳
    model_id: str | None = None  # 会话级模型切换
    skill_id: str | None = None  # 会话级Skill切换，空字符串表示禁用
    thinking_enabled: bool | None = None  # 会话级思考模式切换
    mode: SessionMode | None = None  # ask/agent 模式切换

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_request_id_for_message(self) -> "ChatRequest":
        if self.message is not None and self.request_id is None:
            raise ValueError("request_id is required when message is present")
        if self.message is None and self.request_id is not None:
            raise ValueError("request_id is only valid when message is present")
        return self


class UpdateSessionConfigRequest(BaseModel):
    """更新会话配置"""

    model_id: str | None = None
    skill_id: str | None = None
    thinking_enabled: bool | None = None
    operator_domains: list[str] | None = Field(
        default=None,
        description="Web Operator 域名白名单",
    )


class DecideApprovalRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    feedback: str = Field(default="", max_length=2048)


class DecideApprovalResponse(BaseModel):
    run_id: UUID
    approval_id: UUID
    decision: Literal["approved", "rejected"]


class TokenUsageSummaryResponse(BaseModel):
    """会话 token 用量汇总"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    call_count: int = 0


class TokenUsageRecordResponse(BaseModel):
    """单次 LLM 调用 token 记录"""

    id: str
    agent: str = ""
    step: str = ""
    model_id: str | None = None
    model_name: str = ""
    call_type: str = "stream"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class GetSessionTokenUsageResponse(BaseModel):
    """会话 token 明细响应"""

    summary: TokenUsageSummaryResponse
    records: list[TokenUsageRecordResponse] = Field(default_factory=list)


class ExecutionEventResponse(BaseModel):
    cursor: str
    event_id: UUID
    event_type: str
    run_id: UUID | None = None
    stream_id: str
    stream_version: int
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class GetSessionResponse(BaseModel):
    """获取会话详情响应结构"""

    session_id: str
    title: str | None = None
    status: SessionStatus
    events: list[ExecutionEventResponse] = Field(default_factory=list)
    events_next_cursor: str | None = None
    model_id: str | None = None
    skill_id: str | None = None
    thinking_enabled: bool = False
    model: InferenceModelResponse | None = None
    skill: SkillSummaryResponse | None = None
    token_usage: TokenUsageSummaryResponse | None = None
    operator_scope: str | None = None
    operator_domains: list[str] = Field(default_factory=list)
    mode: SessionMode | None = None
    resource_bindings: list[ResourceBindingProjection] = Field(default_factory=list)


class GetSessionEventsResponse(BaseModel):
    """分页获取会话事件响应结构"""

    events: list[ExecutionEventResponse] = Field(default_factory=list)
    next_cursor: str | None = None
    prev_cursor: str | None = None
    has_earlier: bool = False


class GetSessionFilesResponse(BaseModel):
    """获取会话文件列表响应结构"""

    files: list[File] = Field(default_factory=list)


class FileReadRequest(BaseModel):
    """需要读取的沙箱文件请求结构"""

    filepath: str


class FileReadResponse(BaseModel):
    """需要读取的沙箱文件响应结构体"""

    filepath: str
    content: str


class ShellReadRequest(BaseModel):
    """需要读取的沙箱shell请求结构体"""

    session_id: str  # Shell会话id


class ConsoleRecord(BaseModel):
    """控制台记录模型，包含ps1、command、output"""

    ps1: str
    command: str
    output: str


class ShellReadResponse(BaseModel):
    """需要读取的沙箱shell响应结构体"""

    session_id: str
    output: str
    console_records: list[ConsoleRecord] = Field(default_factory=list)
