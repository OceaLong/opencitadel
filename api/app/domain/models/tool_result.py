from typing import Any, TypeVar

from pydantic import BaseModel, Field, model_validator

from app.domain.models.knowledge_citation import (
    KnowledgeCitation,
    deduplicate_citations,
)
from app.domain.models.tool_execution import (
    ToolExecutionAttempt,
    ToolExecutionStatus,
)

T = TypeVar("T")


def normalize_tool_result(result: Any) -> "ToolResult":
    """Wrap raw tool outputs (e.g. str) into ToolResult for agent processing."""
    if isinstance(result, ToolResult):
        return result
    return ToolResult(success=True, data=result)


class ToolResult[T](BaseModel):
    """工具结果Domain模型"""

    success: bool = True  # 是否成功调用
    message: str | None = ""  # 额外的信息提示
    data: T | None = None  # 工具的执行结果/数据
    status: ToolExecutionStatus | None = None
    attempts: list[ToolExecutionAttempt] = Field(default_factory=list)
    failure_kind: str | None = None
    citations: list[KnowledgeCitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_execution_status(self) -> "ToolResult[T]":
        if self.status is None:
            self.status = (
                ToolExecutionStatus.SUCCESS if self.success else ToolExecutionStatus.FAILED
            )
        self.citations = deduplicate_citations(self.citations)
        return self

    @classmethod
    def from_sandbox(cls, code: int, msg: str, data: T | None, **kwargs) -> "ToolResult":
        """将从沙箱中返回的API数据转换成工具结果"""
        return cls(
            success=code < 300,
            message=msg,
            data=data,
        )
