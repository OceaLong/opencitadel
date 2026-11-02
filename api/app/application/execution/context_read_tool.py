"""Internal read-only tool; its source authority is bound by the Activity adapter."""

from dataclasses import asdict

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import READ_SAFE


class ContextReadTool(BaseTool):
    name = "context"

    def __init__(self, read):
        super().__init__()
        self.read = read

    @tool(
        name="context_read_source",
        description="Read exact original evidence from a source handle in the current context. Returns bounded text and a cursor for the next page.",
        parameters={
            "source_id": {"type": "string"},
            "cursor": {"type": "string"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 65536},
        },
        required=["source_id"],
        policy=READ_SAFE,
    )
    async def read_source(self, source_id: str, cursor: str | None = None, limit: int = 8192):
        page = await self.read(source_id=source_id, cursor=cursor, limit=limit)
        return ToolResult(success=True, data=asdict(page))
