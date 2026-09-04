"""Production Activity handlers admitted by the execution kernel.

装配唯一真源在 ``app.composition.kernel._build_activity_registry``；此处导出
保持与全部 handler 对齐，仅供类型引用与测试使用。
"""

from app.application.execution.activities.child_run import ChildRunActivityHandler
from app.application.execution.activities.model_call import ModelCallActivityHandler
from app.application.execution.activities.patrol import (
    PatrolExecutionActivityHandler,
    PatrolValidationActivityHandler,
)
from app.application.execution.activities.remediation import (
    RemediationActivityHandler,
)
from app.application.execution.activities.resource_build import (
    KnowledgeBuildActivityHandler,
)
from app.application.execution.activities.retrieval import RetrievalActivityHandler
from app.application.execution.activities.tool_call import ToolCallActivityHandler

__all__ = [
    "ChildRunActivityHandler",
    "KnowledgeBuildActivityHandler",
    "ModelCallActivityHandler",
    "PatrolExecutionActivityHandler",
    "PatrolValidationActivityHandler",
    "RemediationActivityHandler",
    "RetrievalActivityHandler",
    "ToolCallActivityHandler",
]
