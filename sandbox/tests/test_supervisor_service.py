"""SupervisorService 行为测试：fake rpc 下的 get_all_processes，以及惰性连接 + 超时状态机。"""

import pytest
from app.interfaces.errors.exceptions import AppException


async def test_construction_is_lazy_and_call_fails_without_socket(supervisor_service):
    """构造函数不应因为/tmp/supervisor.sock不存在而报错（惰性连接，时点挪到首次调用）。"""
    assert supervisor_service.server is None

    with pytest.raises(AppException):
        await supervisor_service.get_all_processes()

    # 首次调用已尝试过连接（惰性连接完成，即便连接对象本身在无真实socket时仍不会立刻报错）
    assert supervisor_service.server is not None


async def test_get_all_processes_uses_fake_rpc(supervisor_service, fake_supervisor_rpc):
    fake_supervisor_rpc.responses["supervisor.getAllProcessInfo"] = [
        {
            "name": "chrome",
            "group": "chrome",
            "description": "d",
            "start": 0,
            "stop": 0,
            "now": 0,
            "state": 20,
            "statename": "RUNNING",
            "spawnerr": "",
            "exitstatus": 0,
            "logfile": "",
            "stdout_logfile": "",
            "stderr_logfile": "",
            "pid": 123,
        }
    ]

    result = await supervisor_service.get_all_processes()

    assert len(result) == 1
    assert result[0].name == "chrome"
    assert ("supervisor.getAllProcessInfo", ()) in fake_supervisor_rpc.calls


async def test_timeout_state_machine_three_states(supervisor_service):
    # 状态1：初始未激活（supervisor_service fixture 将 server_timeout_minutes 收紧为 None）
    status = await supervisor_service.get_timeout_status()
    assert status.active is False

    # 状态2：激活超时销毁
    activated = await supervisor_service.activate_timeout(minutes=5)
    assert activated.active is True
    assert activated.status == "timeout_activated"
    status = await supervisor_service.get_timeout_status()
    assert status.active is True

    # 状态3：取消超时销毁
    cancelled = await supervisor_service.cancel_timeout()
    assert cancelled.status == "timeout_cancelled"
    assert cancelled.active is False
    status = await supervisor_service.get_timeout_status()
    assert status.active is False
