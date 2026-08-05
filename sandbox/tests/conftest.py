#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pytest

from app.services import file as file_module
from app.services.shell import ShellService
from app.services import shell as shell_module
from app.services.supervisor import SupervisorService
from app.services import supervisor as supervisor_module


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    """将FileService的home目录与允许根白名单收紧到一个隔离的临时目录。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(file_module, "SANDBOX_HOME_DIR", str(home))
    monkeypatch.setattr(file_module, "SANDBOX_ALLOWED_ROOTS", (str(home),))
    return home


@pytest.fixture
async def shell_service():
    svc = ShellService()
    yield svc
    for sid in list(svc.active_shells):
        await svc._remove_session(sid)


@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setattr(shell_module, "MAX_ACTIVE_SHELLS", 2)
    monkeypatch.setattr(shell_module, "SHELL_SESSION_TTL_SECONDS", 1)


@pytest.fixture
def supervisor_service(monkeypatch):
    """构造一个不会连接真实supervisor.sock、也不会驻留后台定时器的SupervisorService。"""
    # server_timeout_minutes 默认非None会触发_setup_timer创建后台任务/线程，
    # 测试环境中没有必要保留，直接禁用以避免遗留悬挂的asyncio task/线程。
    from app.core import config as config_module

    class _NoneTimeoutSettings:
        log_level = "INFO"
        server_timeout_minutes = None

    monkeypatch.setattr(config_module, "get_settings", lambda: _NoneTimeoutSettings())
    monkeypatch.setattr(supervisor_module, "get_settings", lambda: _NoneTimeoutSettings())

    svc = SupervisorService()
    yield svc
    if svc.shutdown_task:
        svc.shutdown_task.cancel()
    if svc.shutdown_timer:
        svc.shutdown_timer.cancel()
    # monkeypatch会在本fixture之后自动把get_settings还原为原始的lru_cache函数，
    # 无需(也不能，因为此时它是已被替换的lambda)再手动cache_clear。


@pytest.fixture
def fake_supervisor_rpc(monkeypatch):
    """Stub SupervisorService._call_rpc，让测试无需真实的supervisor.sock即可跑通调用链。

    返回一个带有 .calls（调用记录）和 .responses（method_name -> 返回值,可在用例中改写）
    的命名空间；未在responses中登记的方法名默认返回字符串"ok"。
    """
    calls = []
    responses = {"supervisor.getAllProcessInfo": []}

    async def _fake_call_rpc(self, method_name, *args):
        calls.append((method_name, args))
        return responses.get(method_name, "ok")

    monkeypatch.setattr(SupervisorService, "_call_rpc", _fake_call_rpc)
    return SimpleNamespace(calls=calls, responses=responses)
