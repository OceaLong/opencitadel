"""ShellService 行为测试（侦察 §5.3 B/C/D 点）。"""

import asyncio
import shlex
import sys
from contextlib import suppress

import pytest
from app.interfaces.errors.exceptions import AppException, BadRequestException
from app.services import shell as shell_module
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


def hold_reader(service, monkeypatch, *, session_id=None):
    started, release = asyncio.Event(), asyncio.Event()
    original = service._start_output_reader

    async def read(sid, process):
        if session_id is None or sid == session_id:
            started.set()
            await release.wait()
        await original(sid, process)

    monkeypatch.setattr(service, "_start_output_reader", read)
    return started, release


async def test_completed_command_waits_for_its_stdout_reader(shell_service, monkeypatch):
    started, release = hold_reader(shell_service, monkeypatch)
    task = asyncio.create_task(shell_service.exec_command("owned", "/tmp", "printf owned-output"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(shell_service.active_shells["owned"].process.wait(), 1)
        assert not task.done(), "process exit alone cannot publish a completed command"
        release.set()
        result = await asyncio.wait_for(task, 1)
        assert result.status == "completed"
        assert result.returncode == 0
        assert result.output == "owned-output"
        assert shell_service.active_shells["owned"].console_records[-1].output == result.output
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)


@pytest.mark.parametrize("outcome", ["complete", "timeout", "cancel"])
async def test_wait_process_drains_without_cancelling_background_collection(
    shell_service, monkeypatch, outcome
):
    monkeypatch.setattr(shell_module, "_EXEC_WAIT_SECONDS", 0.01)
    started, release = hold_reader(shell_service, monkeypatch)
    result = await shell_service.exec_command("owned", "/tmp", "printf complete-output")
    assert result.status == "running"
    await asyncio.wait_for(started.wait(), 1)
    process = shell_service.active_shells["owned"].process
    await asyncio.wait_for(process.wait(), 1)
    reader = shell_service._output_readers[process]
    task = asyncio.create_task(
        shell_service.wait_process("owned", seconds=0.01 if outcome == "timeout" else 1)
    )
    try:
        await asyncio.sleep(0)
        assert not task.done()
        if outcome == "complete":
            release.set()
            assert (await asyncio.wait_for(task, 1)).returncode == 0
        elif outcome == "timeout":
            with pytest.raises(BadRequestException, match="等待超时"):
                await asyncio.wait_for(task, 1)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not reader.cancelled()
        release.set()
        await asyncio.wait_for(asyncio.shield(reader), 1)
        assert (await shell_service.wait_process("owned", seconds=1)).returncode == 0
        assert (await shell_service.read_shell_output("owned")).output == "complete-output"
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)


async def test_another_sessions_blocked_reader_does_not_delay_completed_command(
    shell_service, monkeypatch
):
    started, release = hold_reader(shell_service, monkeypatch, session_id="blocked")
    blocked = asyncio.create_task(shell_service.exec_command("blocked", "/tmp", "printf blocked"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        result = await asyncio.wait_for(
            shell_service.exec_command("target", "/tmp", "printf target"), 1
        )
        assert result.status == "completed"
        assert result.output == "target"
        assert not blocked.done()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(blocked, return_exceptions=True), 1)


async def test_descendant_holding_stdout_keeps_wait_bounded_and_recoverable(
    shell_service, monkeypatch, tmp_path
):
    monkeypatch.setattr(shell_module, "_EXEC_WAIT_SECONDS", 0.01)
    release = tmp_path / "release-descendant"
    ready = tmp_path / "descendant-ready"
    child = (
        "import pathlib,sys,threading,time; "
        "release=pathlib.Path(sys.argv[1]); pathlib.Path(sys.argv[2]).touch(); "
        "deadline=time.monotonic()+3; "
        "\nwhile not release.exists() and time.monotonic()<deadline: threading.Event().wait(0.01)"
    )
    parent = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
        "print('owned-pipe',flush=True)"
    )
    command = shlex.join([sys.executable, "-c", parent, child, str(release), str(ready)])
    try:
        result = await asyncio.wait_for(shell_service.exec_command("owned", "/tmp", command), 1)
        assert result.status == "running"
        process = shell_service.active_shells["owned"].process

        async def parent_exited():
            progress = asyncio.Event()
            while process.returncode is None or not ready.exists():
                with suppress(TimeoutError):
                    await asyncio.wait_for(progress.wait(), 0.005)

        await asyncio.wait_for(parent_exited(), 1)
        assert process.returncode == 0
        reader = shell_service._output_readers[process]
        with pytest.raises(BadRequestException, match="等待超时"):
            await asyncio.wait_for(shell_service.wait_process("owned", seconds=0.01), 1)
        assert not reader.done()
        release.touch()
        assert (
            await asyncio.wait_for(shell_service.wait_process("owned", seconds=2), 2)
        ).returncode == 0
        assert (await shell_service.read_shell_output("owned")).output == "owned-pipe\n"
    finally:
        release.touch()
        process = shell_service.active_shells.get("owned")
        if process is not None:
            await asyncio.wait_for(process.process.wait(), 2)


@pytest.mark.parametrize("late_reader", [False, True])
async def test_replaced_command_rejects_old_waiter_and_cannot_pollute_new_output(
    shell_service, monkeypatch, late_reader
):
    monkeypatch.setattr(shell_module, "_READER_CANCEL_TIMEOUT_SECONDS", 0.01)
    read_started, release = asyncio.Event(), asyncio.Event()
    original_create = shell_service._create_process

    async def create(exec_dir, command):
        process = await original_create(exec_dir, command)
        if command == "printf old-output":
            original_read = process.stdout.read

            async def read(size):
                output = await original_read(size)
                if output:
                    read_started.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        if not late_reader:
                            raise
                        await release.wait()
                return output

            monkeypatch.setattr(process.stdout, "read", read)
        return process

    monkeypatch.setattr(shell_service, "_create_process", create)
    old = asyncio.create_task(shell_service.exec_command("owned", "/tmp", "printf old-output"))
    try:
        await asyncio.wait_for(read_started.wait(), 1)
        result = await asyncio.wait_for(
            shell_service.exec_command("owned", "/tmp", "printf new-output"), 1
        )
        assert result.status == "completed"
        assert result.output == "new-output"
        release.set()
        with pytest.raises(AppException, match="replaced"):
            await asyncio.wait_for(old, 1)
        assert (await shell_service.read_shell_output("owned")).output == "new-output"
        records = shell_service.active_shells["owned"].console_records
        assert records[-1].command == "printf new-output"
        assert records[-1].output == "new-output"
        assert len(shell_service._output_readers) == 1
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(old, return_exceptions=True), 1)


async def test_completed_reader_failure_is_retained_and_never_reports_completion(
    shell_service, monkeypatch
):
    original_create = shell_service._create_process

    async def create(exec_dir, command):
        process = await original_create(exec_dir, command)
        original_read = process.stdout.read
        first_read = True

        async def broken_read(size):
            nonlocal first_read
            if first_read:
                first_read = False
                return await original_read(size)
            raise OSError("controlled pipe read failure")

        monkeypatch.setattr(process.stdout, "read", broken_read)
        return process

    monkeypatch.setattr(shell_service, "_create_process", create)
    with pytest.raises(AppException, match="output collection failed"):
        await asyncio.wait_for(shell_service.exec_command("owned", "/tmp", "printf owned"), 1)
    process = shell_service.active_shells["owned"].process
    reader = shell_service._output_readers[process]
    assert reader.done()
    assert reader not in shell_service._reader_tasks
    assert shell_service.active_shells["owned"].output == "owned"
    with pytest.raises(AppException, match="output collection failed"):
        await shell_service.wait_process("owned", seconds=1)
