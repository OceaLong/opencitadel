"""ShellService 行为测试（侦察 §5.3 B/C/D 点）。"""

import asyncio

from app.services.shell import ShellService


def test_remove_ansi_escape_codes():
    text = "\x1b[31mhello\x1b[0m world"
    assert ShellService._remove_ansi_escape_codes(text) == "hello world"


async def test_exec_fast_command_completes(shell_service):
    sid = ShellService.create_session_id()
    result = await shell_service.exec_command(sid, None, "sleep 0.1 && echo done")
    assert result.status == "completed"
    assert result.returncode == 0
    assert "done" in result.output


async def test_exec_slow_command_returns_running(shell_service):
    sid = ShellService.create_session_id()
    result = await shell_service.exec_command(sid, None, "sleep 10")
    assert result.status == "running"
    assert result.returncode is None


async def test_session_capacity_evicts_oldest(shell_service, small_limits):
    sid1 = ShellService.create_session_id()
    await shell_service.exec_command(sid1, None, "echo one")
    await asyncio.sleep(0.05)

    sid2 = ShellService.create_session_id()
    await shell_service.exec_command(sid2, None, "echo two")
    await asyncio.sleep(0.05)

    sid3 = ShellService.create_session_id()
    await shell_service.exec_command(sid3, None, "echo three")

    assert sid1 not in shell_service.active_shells
    assert sid2 in shell_service.active_shells
    assert sid3 in shell_service.active_shells
    assert len(shell_service.active_shells) == 2


async def test_expired_session_cleaned(shell_service, small_limits):
    sid1 = ShellService.create_session_id()
    await shell_service.exec_command(sid1, None, "echo one")

    # SHELL_SESSION_TTL_SECONDS被small_limits收紧为1s，等待超过TTL
    await asyncio.sleep(1.2)

    sid2 = ShellService.create_session_id()
    await shell_service.exec_command(sid2, None, "echo two")

    assert sid1 not in shell_service.active_shells
    assert sid2 in shell_service.active_shells


async def test_kill_running_then_already_terminated(shell_service):
    sid = ShellService.create_session_id()
    await shell_service.exec_command(sid, None, "sleep 10")

    first = await shell_service.kill_process(sid)
    assert first.status == "terminated"

    second = await shell_service.kill_process(sid)
    assert second.status == "already_terminated"
