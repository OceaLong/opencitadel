#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lazy sandbox/browser provisioning for agent tasks."""
import asyncio
import logging
from typing import BinaryIO, Callable, Optional, Type

from app.domain.external.browser import Browser
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class SandboxProvider:
    """Creates or reuses a sandbox on first demand and persists session.sandbox_id."""

    def __init__(
            self,
            session_id: str,
            sandbox_id: Optional[str],
            sandbox_cls: Type[Sandbox],
            uow_factory: Callable[[], IUnitOfWork],
    ) -> None:
        self._session_id = session_id
        self._initial_sandbox_id = sandbox_id
        self._sandbox_cls = sandbox_cls
        self._uow_factory = uow_factory
        self._lock = asyncio.Lock()
        self._sandbox: Optional[Sandbox] = None

    def materialized(self) -> Optional[Sandbox]:
        """Return the live sandbox instance without triggering creation."""
        return self._sandbox

    async def get(self) -> Sandbox:
        if self._sandbox is not None:
            return self._sandbox
        async with self._lock:
            if self._sandbox is not None:
                return self._sandbox
            sandbox: Optional[Sandbox] = None
            if self._initial_sandbox_id:
                sandbox = await self._sandbox_cls.get(self._initial_sandbox_id)
            if sandbox is None:
                logger.info("按需创建沙箱: session=%s", self._session_id)
                sandbox = await self._sandbox_cls.create()
                async with self._uow_factory() as uow:
                    session = await uow.session.get_by_id(self._session_id)
                    if session:
                        session.sandbox_id = sandbox.id
                        await uow.session.save(session)
            self._sandbox = sandbox
            return sandbox


class LazySandbox:
    """Deferred Sandbox proxy; materializes on first tool invocation."""

    def __init__(self, provider: SandboxProvider) -> None:
        self._provider = provider

    @property
    def provider(self) -> SandboxProvider:
        return self._provider

    @property
    def id(self) -> str:
        materialized = self._provider.materialized()
        if materialized is not None:
            return materialized.id
        return self._provider._initial_sandbox_id or "pending-sandbox"

    @property
    def vnc_url(self) -> str:
        materialized = self._provider.materialized()
        return materialized.vnc_url if materialized is not None else ""

    @property
    def cdp_url(self) -> str:
        materialized = self._provider.materialized()
        return materialized.cdp_url if materialized is not None else ""

    async def _resolve(self) -> Sandbox:
        return await self._provider.get()

    async def exec_command(self, session_id: str, exec_dir: str, command: str) -> ToolResult:
        return await (await self._resolve()).exec_command(session_id, exec_dir, command)

    async def read_shell_output(self, session_id: str, console: bool = False) -> ToolResult:
        return await (await self._resolve()).read_shell_output(session_id, console=console)

    async def wait_process(self, session_id: str, seconds: Optional[int] = None) -> ToolResult:
        return await (await self._resolve()).wait_process(session_id, seconds=seconds)

    async def write_shell_input(
            self,
            session_id: str,
            input_text: str,
            press_enter: bool = True,
    ) -> ToolResult:
        return await (await self._resolve()).write_shell_input(
            session_id, input_text, press_enter=press_enter,
        )

    async def kill_process(self, session_id: str) -> ToolResult:
        return await (await self._resolve()).kill_process(session_id)

    async def write_file(
            self,
            filepath: str,
            content: str,
            append: bool = False,
            leading_newline: bool = False,
            trailing_newline: bool = False,
            sudo: bool = False,
    ) -> ToolResult:
        return await (await self._resolve()).write_file(
            filepath, content, append=append,
            leading_newline=leading_newline, trailing_newline=trailing_newline, sudo=sudo,
        )

    async def read_file(
            self,
            filepath: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
            sudo: bool = False,
            max_length: int = 10000,
    ) -> ToolResult:
        return await (await self._resolve()).read_file(
            filepath, start_line=start_line, end_line=end_line, sudo=sudo, max_length=max_length,
        )

    async def check_file_exists(self, filepath: str) -> ToolResult:
        return await (await self._resolve()).check_file_exists(filepath)

    async def delete_file(self, filepath: str) -> ToolResult:
        return await (await self._resolve()).delete_file(filepath)

    async def list_files(self, dir_path: str) -> ToolResult:
        return await (await self._resolve()).list_files(dir_path)

    async def replace_in_file(
            self,
            filepath: str,
            old_str: str,
            new_str: str,
            sudo: bool = False,
    ) -> ToolResult:
        return await (await self._resolve()).replace_in_file(
            filepath, old_str, new_str, sudo=sudo,
        )

    async def search_in_file(self, filepath: str, regex: str, sudo: bool = False) -> ToolResult:
        return await (await self._resolve()).search_in_file(filepath, regex, sudo=sudo)

    async def find_files(self, dir_path: str, glob_pattern: str) -> ToolResult:
        return await (await self._resolve()).find_files(dir_path, glob_pattern)

    async def upload_file(
            self,
            file_data: BinaryIO,
            filepath: str,
            filename: str = None,
    ) -> ToolResult:
        return await (await self._resolve()).upload_file(file_data, filepath, filename=filename)

    async def download_file(self, filepath: str) -> BinaryIO:
        return await (await self._resolve()).download_file(filepath)

    async def ensure_sandbox(self, max_retries: Optional[int] = None) -> None:
        await (await self._resolve()).ensure_sandbox(max_retries=max_retries)

    async def destroy(self) -> bool:
        materialized = self._provider.materialized()
        if materialized is None:
            return True
        return await materialized.destroy()

    async def get_browser(self, supports_multimodal: bool = False) -> Browser:
        return await (await self._resolve()).get_browser(supports_multimodal=supports_multimodal)

    async def create_workspace_snapshot(self, snapshot_id: str) -> bytes:
        return await (await self._resolve()).create_workspace_snapshot(snapshot_id)

    async def restore_workspace_snapshot(self, snapshot_id: str, snapshot_data: BinaryIO) -> None:
        await (await self._resolve()).restore_workspace_snapshot(snapshot_id, snapshot_data)


class LazyBrowser:
    """Deferred Browser proxy; materializes when a browser tool is invoked."""

    def __init__(self, provider: SandboxProvider, supports_multimodal: bool = False) -> None:
        self._provider = provider
        self._supports_multimodal = supports_multimodal
        self._inner: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def _resolve(self) -> Browser:
        if self._inner is not None:
            return self._inner
        async with self._lock:
            if self._inner is not None:
                return self._inner
            sandbox = await self._provider.get()
            browser = await sandbox.get_browser(supports_multimodal=self._supports_multimodal)
            if browser is None:
                raise RuntimeError(f"获取沙箱[{sandbox.id}]中的浏览器实例失败")
            self._inner = browser
            return browser

    async def cleanup(self) -> None:
        if self._inner is not None:
            await self._inner.cleanup()

    async def view_page(self) -> ToolResult:
        return await (await self._resolve()).view_page()

    async def navigate(self, url: str) -> ToolResult:
        return await (await self._resolve()).navigate(url)

    async def restart(self, url: str) -> ToolResult:
        return await (await self._resolve()).restart(url)

    async def click(
            self,
            index: Optional[int] = None,
            coordinate_x: Optional[float] = None,
            coordinate_y: Optional[float] = None,
    ) -> ToolResult:
        return await (await self._resolve()).click(index, coordinate_x, coordinate_y)

    async def input(
            self,
            text: str,
            press_enter: bool,
            index: Optional[int] = None,
            coordinate_x: Optional[float] = None,
            coordinate_y: Optional[float] = None,
    ) -> ToolResult:
        return await (await self._resolve()).input(
            text, press_enter, index, coordinate_x, coordinate_y,
        )

    async def move_mouse(self, coordinate_x: float, coordinate_y: float) -> ToolResult:
        return await (await self._resolve()).move_mouse(coordinate_x, coordinate_y)

    async def press_key(self, key: str) -> ToolResult:
        return await (await self._resolve()).press_key(key)

    async def select_option(self, index: int, option: int) -> ToolResult:
        return await (await self._resolve()).select_option(index, option)

    async def scroll_up(self, to_top: Optional[bool] = None) -> ToolResult:
        return await (await self._resolve()).scroll_up(to_top)

    async def scroll_down(self, to_down: Optional[bool] = None) -> ToolResult:
        return await (await self._resolve()).scroll_down(to_down)

    async def screenshot(self, full_page: Optional[bool] = None) -> bytes:
        return await (await self._resolve()).screenshot(full_page)

    async def take_screenshot(self) -> ToolResult:
        return await (await self._resolve()).take_screenshot()

    async def console_exec(self, javascript: str) -> ToolResult:
        return await (await self._resolve()).console_exec(javascript)

    async def console_view(self, max_lines: Optional[int] = None) -> ToolResult:
        return await (await self._resolve()).console_view(max_lines)
