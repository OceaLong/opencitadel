import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from .codebase import SessionMode
from .file import File
from .operator import normalize_operator_domains
from .resource_bindings import ResourceBindingProjection, ResourceKind


class SessionStatus(StrEnum):
    """会话状态类型枚举"""

    PENDING = "pending"  # 等待任务
    RUNNING = "running"  # 运行中
    WAITING = "waiting"  # 等待人类响应
    COMPLETED = "completed"  # 已完成
    CANCELLED = "cancelled"  # 用户取消
    FAILED = "failed"  # 执行失败


class Session(BaseModel):
    """会话领域模型"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # 会话id
    sandbox_id: str | None = None  # 沙箱id
    title: str = ""  # 标题
    unread_message_count: int = 0  # 未读消息数
    latest_message: str = ""  # 最新消息
    latest_message_at: datetime | None = None  # 最新消息时间
    files: list[File] = Field(default_factory=list)  # 文件列表
    model_id: str | None = None  # 会话级模型id，null使用全局默认
    skill_id: str | None = None  # 会话级Skill id，null表示不启用
    thinking_enabled: bool = False  # 会话级思考模式，默认关闭
    resource_bindings: list[ResourceBindingProjection] = Field(
        default_factory=list
    )  # 当前不可变资源版本投影；历史回答以事件快照为准
    owner_user_id: str | None = None  # 所属用户
    team_id: str | None = None  # 所属团队工作区
    mode: SessionMode = SessionMode.AGENT  # ask=快速问答, agent=规划改码
    operator_scope: Literal["owned", "third_party_saas"] | None = None
    operator_domains: list[str] = Field(default_factory=list)  # 域名白名单
    status: SessionStatus = SessionStatus.PENDING  # 状态
    active_execution_run_id: UUID | None = None
    active_execution_request_id: UUID | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))  # 更新时间
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))  # 创建时间

    @field_validator("operator_domains")
    @classmethod
    def validate_operator_domains(cls, value: list[str]) -> list[str]:
        return normalize_operator_domains(value)

    @model_validator(mode="after")
    def validate_operator_boundary(self) -> "Session":
        if self.operator_scope is not None and not self.operator_domains:
            raise ValueError("operator sessions require at least one allowed domain")
        return self

    def binding_for(
        self,
        kind: ResourceKind,
    ) -> ResourceBindingProjection | None:
        """Return the sole current binding of ``kind`` for this session."""
        matches = [binding for binding in self.resource_bindings if binding.resource_kind == kind]
        if len(matches) > 1:
            raise ValueError(f"会话[{self.id}]存在重复的[{kind.value}]资源绑定")
        return matches[0] if matches else None
