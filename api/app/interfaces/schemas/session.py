#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.models.file import File
from app.domain.models.session import SessionStatus
from app.interfaces.schemas.event import AgentSSEEvent
from app.interfaces.schemas.skill import SkillSummaryResponse
from app.interfaces.schemas.llm_model import LLMModelResponse


class CreateSessionRequest(BaseModel):
    """创建会话请求"""
    title: Optional[str] = None
    model_id: Optional[str] = None
    skill_id: Optional[str] = None
    thinking_enabled: Optional[bool] = None


class CreateSessionResponse(BaseModel):
    """创建会话响应结构"""
    session_id: str  # 会话id


class ListSessionItem(BaseModel):
    """会话列表条目基础信息"""
    session_id: str = ""
    title: str = ""
    latest_message: str = ""
    latest_message_at: Optional[datetime] = Field(default_factory=datetime.now)
    status: SessionStatus = SessionStatus.PENDING
    unread_message_count: int = 0


class ListSessionResponse(BaseModel):
    """获取会话列表基础信息响应结构"""
    sessions: List[ListSessionItem]


class ChatRequest(BaseModel):
    """聊天请求结构"""
    message: Optional[str] = None  # 人类消息
    attachments: Optional[List[str]] = Field(default_factory=list)  # 附件列表(传递的是文件id列表)
    event_id: Optional[str] = None  # 最新事件id
    timestamp: Optional[int] = None  # 当前时间戳
    model_id: Optional[str] = None  # 会话级模型切换
    skill_id: Optional[str] = None  # 会话级Skill切换，空字符串表示禁用
    thinking_enabled: Optional[bool] = None  # 会话级思考模式切换


class UpdateSessionConfigRequest(BaseModel):
    """更新会话配置"""
    model_id: Optional[str] = None
    skill_id: Optional[str] = None
    thinking_enabled: Optional[bool] = None


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
    model_id: Optional[str] = None
    model_name: str = ""
    call_type: str = "stream"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    created_at: datetime = Field(default_factory=datetime.now)


class GetSessionTokenUsageResponse(BaseModel):
    """会话 token 明细响应"""
    summary: TokenUsageSummaryResponse
    records: List[TokenUsageRecordResponse] = Field(default_factory=list)


class GetSessionResponse(BaseModel):
    """获取会话详情响应结构"""
    session_id: str
    title: Optional[str] = None
    status: SessionStatus
    events: List[AgentSSEEvent] = Field(default_factory=list)
    model_id: Optional[str] = None
    skill_id: Optional[str] = None
    thinking_enabled: bool = False
    model: Optional[LLMModelResponse] = None
    skill: Optional[SkillSummaryResponse] = None
    token_usage: Optional[TokenUsageSummaryResponse] = None


class GetSessionFilesResponse(BaseModel):
    """获取会话文件列表响应结构"""
    files: List[File] = Field(default_factory=list)


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
    console_records: List[ConsoleRecord] = Field(default_factory=list)
