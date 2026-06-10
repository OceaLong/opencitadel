#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Enrich tool events with UI-facing content (screenshots, file previews, etc.)."""
import base64
import io
import logging
import uuid
from typing import Callable, Optional

from app.domain.external.browser import Browser
from app.domain.external.file_storage import FileStorage, FileUploadPayload
from app.domain.external.sandbox import Sandbox
from app.domain.models.event import (
    A2AToolContent,
    BrowserToolContent,
    FileToolContent,
    MCPToolContent,
    SearchToolContent,
    ShellToolContent,
    ToolEvent,
    ToolEventStatus,
)
from app.domain.models.search import SearchResults
from app.domain.models.tool_result import ToolResult
from core.config import get_settings

logger = logging.getLogger(__name__)

FILE_MUTATING_FUNCTIONS = frozenset({"write_file", "replace_in_file"})


class ToolEventPresenter:
    """Build rich tool_content payloads for the frontend."""

    def __init__(
            self,
            sandbox: Sandbox,
            browser: Browser,
            file_storage: FileStorage,
            sync_file_to_storage: Callable,
            get_stream_size: Callable,
    ) -> None:
        self._sandbox = sandbox
        self._browser = browser
        self._file_storage = file_storage
        self._sync_file_to_storage = sync_file_to_storage
        self._get_stream_size = get_stream_size

    async def enrich(self, event: ToolEvent) -> None:
        if event.status != ToolEventStatus.CALLED:
            return
        try:
            if event.tool_name == "browser":
                event.tool_content = BrowserToolContent(
                    screenshot=await self._get_browser_screenshot(event),
                )
            elif event.tool_name == "search":
                search_results: ToolResult[SearchResults] = event.function_result
                if (
                        search_results
                        and search_results.success
                        and search_results.data
                        and search_results.data.results is not None
                ):
                    event.tool_content = SearchToolContent(results=search_results.data.results)
                else:
                    event.tool_content = SearchToolContent(results=[])
            elif event.tool_name == "shell":
                console = self._get_tool_result_data(event).get("console_records")
                if console is not None:
                    event.tool_content = ShellToolContent(console=console)
                elif "session_id" in event.function_args:
                    shell_result = await self._sandbox.read_shell_output(
                        event.function_args["session_id"],
                        console=True,
                    )
                    event.tool_content = ShellToolContent(
                        console=(shell_result.data or {}).get("console_records", [])
                    )
                else:
                    event.tool_content = ShellToolContent(console="(No console)")
            elif event.tool_name == "file":
                await self._enrich_file_event(event)
            elif event.tool_name in ("mcp", "a2a"):
                await self._enrich_mcp_a2a_event(event)
        except Exception as e:
            logger.exception("ToolEventPresenter enrich failed: %s", e)

    @staticmethod
    def _get_tool_result_data(event: ToolEvent) -> dict:
        data = event.function_result.data if event.function_result else None
        return data if isinstance(data, dict) else {}

    async def _enrich_file_event(self, event: ToolEvent) -> None:
        result_data = self._get_tool_result_data(event)
        file_content = result_data.get("content", "")
        filepath = event.function_args.get("filepath")
        event.tool_content = FileToolContent(content=file_content or "(No Content)")
        should_sync = (
                event.function_name in FILE_MUTATING_FUNCTIONS
                and event.function_result
                and event.function_result.success
                and filepath
        )
        if should_sync:
            file_read_result = await self._sandbox.read_file(filepath)
            if file_read_result.success:
                file_content = (file_read_result.data or {}).get("content", "")
                event.tool_content = FileToolContent(content=file_content or "(No Content)")
            await self._sync_file_to_storage(filepath)

    async def _enrich_mcp_a2a_event(self, event: ToolEvent) -> None:
        if not event.function_result:
            event.tool_content = (
                MCPToolContent(result="(MCP工具无可用结果)")
                if event.tool_name == "mcp"
                else A2AToolContent(a2a_result="(A2A智能体无可用结果)")
            )
            return
        if hasattr(event.function_result, "data") and event.function_result.data:
            data = event.function_result.data
        elif hasattr(event.function_result, "success") and event.function_result.success:
            data = (
                event.function_result.model_dump()
                if hasattr(event.function_result, "model_dump")
                else str(event.function_result)
            )
        else:
            data = str(event.function_result)
        event.tool_content = (
            MCPToolContent(result=data)
            if event.tool_name == "mcp"
            else A2AToolContent(a2a_result=data)
        )

    async def _get_browser_screenshot(self, event: ToolEvent) -> str:
        result_data = self._get_tool_result_data(event)
        screenshot_base64 = result_data.get("screenshot_base64")
        if not screenshot_base64:
            return ""
        screenshot = base64.b64decode(screenshot_base64)
        stream = io.BytesIO(screenshot)
        file = await self._file_storage.upload_file(FileUploadPayload(
            file=stream,
            filename=f"{uuid.uuid4()}.png",
            size=self._get_stream_size(stream),
        ))
        settings = get_settings()
        return f"https://{settings.cos_bucket}.cos.{settings.cos_region}.myqcloud.com/{file.key}"
