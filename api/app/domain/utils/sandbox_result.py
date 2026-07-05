#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Helpers for parsing structured ToolResult payloads from sandbox APIs."""
from typing import Any, Dict, Optional

from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult


def shell_exec_data(result: ToolResult) -> Dict[str, Any]:
    return result.data if isinstance(result.data, dict) else {}


def shell_output(result: ToolResult) -> str:
    data = result.data
    if isinstance(data, dict):
        return data.get("output") or ""
    return data if isinstance(data, str) else ""


def file_content(result: ToolResult) -> str:
    data = result.data
    if isinstance(data, dict):
        return data.get("content") or ""
    return data if isinstance(data, str) else ""


def _summarize_output(output: str, limit: int = 500) -> str:
    text = (output or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _raise_command_failed(command: str, message: Optional[str], output: str, returncode: Optional[int]) -> None:
    detail = _summarize_output(output)
    parts = [f"命令执行失败: {command}"]
    if returncode is not None:
        parts.append(f"exit={returncode}")
    if message:
        parts.append(message)
    if detail:
        parts.append(detail)
    raise RuntimeError(" | ".join(parts))


async def exec_command_await(
        sandbox: Sandbox,
        session_id: str,
        exec_dir: str,
        command: str,
        *,
        timeout: int = 120,
) -> str:
    """Run a shell command and wait for completion, returning stdout."""
    result = await sandbox.exec_command(session_id, exec_dir, command)
    if not result.success:
        _raise_command_failed(command, result.message, shell_output(result), None)

    data = shell_exec_data(result)
    status = data.get("status")

    if status == "running":
        wait_result = await sandbox.wait_process(session_id, seconds=timeout)
        if not wait_result.success:
            _raise_command_failed(command, wait_result.message, shell_output(result), None)
        wait_data = shell_exec_data(wait_result)
        returncode = wait_data.get("returncode")
        output_result = await sandbox.read_shell_output(session_id)
        output = shell_output(output_result) if output_result.success else shell_output(result)
        if returncode not in (0, None):
            _raise_command_failed(command, result.message, output, returncode)
        return output

    if status == "completed":
        returncode = data.get("returncode")
        output = data.get("output") or ""
        if returncode not in (0, None):
            _raise_command_failed(command, result.message, output, returncode)
        return output

    output = shell_output(result)
    if output:
        return output
    _raise_command_failed(command, result.message or f"未知状态: {status}", output, data.get("returncode"))
