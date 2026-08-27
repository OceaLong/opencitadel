from typing import BinaryIO, Protocol

from app.domain.external.browser import Browser
from app.domain.external.llm import LLM
from app.domain.models.scope import OwnerScope
from app.domain.models.tool_result import ToolResult


class Sandbox(Protocol):
    """沙箱服务扩展协议，包含文件工具协议、Shell工具协议以及沙箱本身的扩展"""

    async def exec_command(self, session_id: str, exec_dir: str, command: str) -> ToolResult:
        """根据传递的会话id+目录+命令执行对应的命令"""
        ...

    async def read_shell_output(self, session_id: str, console: bool = False) -> ToolResult:
        """根据传递的会话id+是否返回控制台记录获取shell结果"""
        ...

    async def wait_process(self, session_id: str, seconds: int | None = None) -> ToolResult:
        """根据传递的会话id+秒数等待程序执行"""
        ...

    async def write_shell_input(
        self,
        session_id: str,
        input_text: str,
        press_enter: bool = True,
    ) -> ToolResult:
        """根据传递会话id+文本内容+是否回车键写入内容到进程中"""
        ...

    async def kill_process(self, session_id: str) -> ToolResult:
        """根据传递的会话id杀死对应的进程"""
        ...

    async def write_file(
        self,
        filepath: str,
        content: str,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
        sudo: bool = False,
    ) -> ToolResult:
        """根据传递的文件路径+写入内容+追加模式+前后内容新行+超级权限写入对应的文件"""
        ...

    async def read_file(
        self,
        filepath: str,
        start_line: int | None = None,
        end_line: int | None = None,
        sudo: bool = False,
        max_length: int = 10000,
    ) -> ToolResult:
        """根据传递的文件路径+起点终点行数+超级权限读取对应的文件内容"""
        ...

    async def read_files(
        self,
        filepaths: list[str],
        *,
        sudo: bool = False,
        max_length: int = 10000,
    ) -> list[ToolResult]:
        """Batch-read sandbox files with one per-file byte cap."""
        ...

    async def check_file_exists(self, filepath: str) -> ToolResult:
        """根据传递的文件路径判断文件是否存在"""
        ...

    async def delete_file(self, filepath: str) -> ToolResult:
        """根据传递的文件路径删除指定文件"""
        ...

    async def list_files(self, dir_path: str) -> ToolResult:
        """根据传递的文件夹路径列出该路径下的所有文件"""
        ...

    async def replace_in_file(
        self,
        filepath: str,
        old_str: str,
        new_str: str,
        sudo: bool = False,
    ) -> ToolResult:
        """根据传递文件路径+新旧内容+超级权限完成文件内容替换"""
        ...

    async def search_in_file(self, filepath: str, regex: str, sudo: bool = False) -> ToolResult:
        """根据传递的文件路径+正则+超级权限完成文件内容检索"""
        ...

    async def find_files(self, dir_path: str, glob_pattern: str) -> ToolResult:
        """根据传递的文件夹路径+匹配规则查找文件"""
        ...

    async def upload_file(
        self,
        file_data: BinaryIO,
        filepath: str,
        filename: str | None = None,
    ) -> ToolResult:
        """根据文件源数据+路径+文件名将文件上传到沙箱中"""
        ...

    async def download_file(self, filepath: str) -> BinaryIO:
        """根据传递的文件路径下载沙箱中的文件"""
        ...

    async def create_workspace_snapshot(self, snapshot_id: str) -> bytes:
        """Create a tar.gz snapshot of the sandbox workspace."""
        ...

    async def restore_workspace_snapshot(self, snapshot_id: str, snapshot_data: BinaryIO) -> None:
        """Restore a tar.gz snapshot into the sandbox workspace."""
        ...

    async def ensure_sandbox(self) -> None:
        """确保当前沙箱存在，如果不存在会创建"""
        ...

    async def destroy(self) -> bool:
        """销毁当前沙箱实例"""
        ...

    async def get_browser(
        self,
        llm: LLM | None = None,
        allowed_domains: frozenset[str] | None = None,
    ) -> Browser:
        """获取沙箱中的浏览器实例"""
        ...

    @property
    def id(self) -> str:
        """只读属性，返回沙箱的id"""
        ...

    @property
    def cdp_url(self) -> str:
        """只读属性，返回沙箱的cdp链接(操控浏览器的)"""
        ...

    @property
    def vnc_url(self) -> str:
        """只读属性，获取沙箱的vnc链接(远程桌面链接)"""
        ...

    async def create_browser_profile_snapshot(self, snapshot_id: str) -> bytes:
        """Create a tar.gz snapshot of the browser profile directory."""
        ...

    async def restore_browser_profile_snapshot(
        self, snapshot_id: str, snapshot_data: BinaryIO
    ) -> None:
        """Restore the browser profile directory from a tar.gz snapshot."""
        ...

    async def restart_browser(self) -> None:
        """Restart the sandbox Chrome process after profile restore."""
        ...


class SandboxFactoryPort(Protocol):
    """Owner-aware lifecycle boundary used by application/domain services."""

    async def create(self, *, owner_scope: OwnerScope) -> Sandbox: ...

    async def get(self, sandbox_id: str) -> Sandbox | None: ...
