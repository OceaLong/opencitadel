import uuid
from collections.abc import Awaitable, Callable

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import WORKSPACE_WRITE

WriteArtifactFn = Callable[..., Awaitable[dict]]
FinalizeArtifactFn = Callable[[str], Awaitable[dict]]


def _normalize_artifact_id(artifact_id: str | None, *, required: bool = False) -> str | None:
    if artifact_id is None or not str(artifact_id).strip():
        if required:
            raise ValueError("artifact_id 不能为空")
        return None
    normalized = str(artifact_id).strip()
    try:
        uuid.UUID(normalized)
    except ValueError as exc:
        # 该 ValueError 在 artifact_write/finalize 内部即被归一化为失败的
        # ToolResult（面向模型的纠错提示），无需升格为 ToolInvocationError。
        raise ValueError(
            f"无效的 artifact_id[{normalized}]；创建新交付物请留空 artifact_id，"
            f"更新已有交付物请使用 artifact_write 返回的 id"
        ) from exc
    return normalized


class ArtifactTool(BaseTool):
    name: str = "artifact"

    def __init__(
        self,
        write_fn: WriteArtifactFn,
        finalize_fn: FinalizeArtifactFn,
    ) -> None:
        super().__init__()
        self._write_fn = write_fn
        self._finalize_fn = finalize_fn

    @tool(
        name="artifact_write",
        description=(
            "创建或更新会话交付物（文档 Markdown 或网页 HTML）。产出最终结果时必须使用此工具，而非 write_file。"
            "长文档应先用 write_file 写入沙箱，再传 source_path 引用该文件；不要内联大段 content。"
        ),
        parameters={
            "artifact_id": {
                "type": "string",
                "description": (
                    "已有交付物 ID；留空则创建新交付物。"
                    "更新时必须使用上次 artifact_write 成功返回的 id，不可自行编造"
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["doc", "web"],
                "description": "交付物类型：doc=Markdown 文档，web=HTML 网页",
            },
            "title": {"type": "string", "description": "交付物标题"},
            "content": {
                "type": "string",
                "description": "短内容可直接内联（Markdown 或 HTML）；长文档请改用 source_path",
            },
            "source_path": {
                "type": "string",
                "description": "沙箱内源文件绝对路径；长文档优先使用此参数而非 content",
            },
        },
        required=["kind", "title"],
        policy=WORKSPACE_WRITE,
    )
    async def artifact_write(
        self,
        kind: str,
        title: str,
        content: str | None = None,
        artifact_id: str | None = None,
        source_path: str | None = None,
    ) -> ToolResult:
        if not content and not source_path:
            return ToolResult(
                success=False,
                message="artifact_write 需要 content 或 source_path 至少其一",
            )
        try:
            normalized_id = _normalize_artifact_id(artifact_id)
            data = await self._write_fn(
                artifact_id=normalized_id,
                kind=kind,
                title=title,
                content=content or "",
                source_path=source_path,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return ToolResult(success=False, message=str(exc))
        saved_id = data.get("id", "")
        saved_title = data.get("title", title)
        return ToolResult(
            success=True,
            message=f"交付物已保存 (id={saved_id}): {saved_title}",
            data=data,
        )

    @tool(
        name="artifact_finalize",
        description="将交付物标记为定稿，不再自动更新",
        parameters={
            "artifact_id": {
                "type": "string",
                "description": "交付物 ID（必须使用 artifact_write 返回的 id）",
            },
        },
        required=["artifact_id"],
        policy=WORKSPACE_WRITE,
    )
    async def artifact_finalize(self, artifact_id: str) -> ToolResult:
        try:
            normalized_id = _normalize_artifact_id(artifact_id, required=True)
            data = await self._finalize_fn(normalized_id)
        except (OSError, RuntimeError, ValueError) as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(success=True, message="交付物已定稿", data=data)
