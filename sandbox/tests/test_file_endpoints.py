#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""download-file 端点的路径一致性测试。

此前 `download_file` 端点在 `ensure_file(filepath)` 校验通过后，仍用调用方原始的
（未归一化）filepath 构造 `FileResponse`——校验的是一个路径，实际服务的是另一个路径。
对于合法的相对路径（例如 "foo.txt"），`ensure_file` 内部会把它归一化到
`SANDBOX_HOME_DIR` 下再判断是否存在，但 `FileResponse(path=filepath, ...)` 用的是原始
相对路径，其解析依赖进程当前工作目录（不等于 SANDBOX_HOME_DIR），导致下载时读不到文件。

本文件直接调用 `download_file` 这个端点函数（不经过FastAPI路由/DI），验证：
1. 合法相对路径经端点能正确取到文件内容；
2. 恶意逃逸路径仍被拒绝（BadRequestException）。
"""
import pytest

from app.interfaces.endpoints.file import download_file
from app.interfaces.errors.exceptions import BadRequestException
from app.services.file import FileService


async def _read_file_response_body(response) -> bytes:
    """驱动Starlette FileResponse的ASGI调用协议，拿到它真正会发送的响应体字节。"""
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request"}

    scope = {"type": "http", "method": "GET", "extensions": {}, "headers": []}
    await response(scope, receive, send)

    return b"".join(
        message.get("body", b"")
        for message in messages
        if message.get("type") == "http.response.body"
    )


async def test_download_file_returns_correct_content_for_relative_path(sandbox_home):
    (sandbox_home / "download_target.txt").write_text("hello download")

    response = await download_file(filepath="download_target.txt", file_service=FileService())

    # response.path 必须是归一化后的绝对路径，而不是调用方原始的相对路径
    assert response.path == str(sandbox_home / "download_target.txt")

    body = await _read_file_response_body(response)
    assert body == b"hello download"


async def test_download_file_rejects_escape(sandbox_home):
    with pytest.raises(BadRequestException):
        await download_file(filepath="../../etc/passwd", file_service=FileService())
