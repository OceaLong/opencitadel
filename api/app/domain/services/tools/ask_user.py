"""Clarification tool: the model asks the human to pick among options."""

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import CLARIFICATION_INTERACTIVE


class AskUserTool(BaseTool):
    """让模型在需求不清晰时向用户发起澄清选择。

    该工具的执行路径完全复用审批等待机制（policy=CLARIFICATION_INTERACTIVE）：
    模型调用它 → Run 进入 WAITING(approval)，前端渲染"澄清选项卡片"（问题 +
    推荐选项按钮）→ 用户点选即 approve 且所选项作为 feedback →恢复执行时
    feedback 注入 ``resolved_choice``，本方法把它作为工具结果回流给模型。
    """

    name: str = "ask_user"

    @tool(
        name="ask_user",
        description=(
            "当用户的需求存在歧义、缺少关键信息或有多种合理方案时，"
            "调用此工具向用户发起澄清。提供一个明确的问题和 2-6 个具体的"
            "推荐选项，等待用户选择后再继续执行。不要在需求明确时使用。"
        ),
        parameters={
            "question": {
                "type": "string",
                "description": "向用户提出的澄清问题，具体说明当前的歧义点。",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-6 个具体、互斥的推荐方案，每项一句话说清做法。",
            },
            "resolved_choice": {
                "type": "string",
                "description": "（系统注入）用户选择的方案原文，模型无需填写。",
            },
        },
        required=["question", "options"],
        policy=CLARIFICATION_INTERACTIVE,
    )
    async def ask_user(
        self,
        question: str,
        options: list[str],
        resolved_choice: str | None = None,
    ) -> ToolResult[dict]:
        """把用户在澄清卡片上的选择作为工具结果返还给模型。"""
        if not resolved_choice:
            # 正常流程下 resolved_choice 由审批 feedback 注入；缺失说明
            # 用户批准但未选择（或链路异常），如实告知模型而非猜测。
            return ToolResult(
                success=True,
                message="用户批准继续，但未选择具体方案；请按最合理的默认方案执行并说明。",
                data={"question": question, "options": options, "choice": None},
            )
        return ToolResult(
            success=True,
            message=f"用户选择了：{resolved_choice}",
            data={"question": question, "options": options, "choice": resolved_choice},
        )


__all__ = ["AskUserTool"]
