import asyncio
import http.client
import logging
import socket
import threading
import xmlrpc.client
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.interfaces.errors.exceptions import AppException, BadRequestException
from app.models.supervisor import ProcessInfo, SupervisorActionResult, SupervisorTimeout

"""
1.Supervisor启动后，通过一个Unix套接字文件来实现通信(rpc协议)
2.连接这个通信文件，/tmp/supervisor.sock (xml-rpc连接)
3.使用某种方式来完整转换，让xml-rpc实现连接supervisor.sock
4.连接之后我们就可以调用rpc对应的方法，getAllProcessInfo()
"""

logger = logging.getLogger(__name__)


class UnixStreamHTTPConnection(http.client.HTTPConnection):
    """基于Unix流的HTTP连接处理器"""

    def __init__(self, host: str, socket_path: str, timeout=None) -> None:
        """构造函数，完成连接处理器初始化"""
        http.client.HTTPConnection.__init__(self, host, timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        """重写连接方法，欺骗xml-rpc库让其觉得自己正在进行网络连接"""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


class UnixStreamTransport(xmlrpc.client.Transport):
    """基于Unix流传输层的适配器/转换器"""

    def __init__(self, socket_path: str) -> None:
        """构造函数，完成传输适配器的初始化"""
        xmlrpc.client.Transport.__init__(self)
        self.socket_path = socket_path

    def make_connection(self, host) -> http.client.HTTPConnection:
        return UnixStreamHTTPConnection(host, self.socket_path)


class SupervisorService:
    """Supervisor服务"""

    def __init__(self) -> None:
        """构造函数，完成supervisor服务初始化（rpc连接惰性化，首次调用时才真正连接）"""
        # 1.连接supervisor配置（惰性：此处只记录socket路径，不在构造时连接，
        #   使得没有supervisor.sock的环境（如CI）也能实例化该服务）
        self.rpc_url = "/tmp/supervisor.sock"
        self.server = None

        # 2.supervisor超时配置
        settings = get_settings()
        self.timeout_active = settings.server_timeout_minutes is not None
        self.shutdown_task = None
        self.shutdown_time = None
        self.shutdown_timer = None
        self._expand_enabled = True  # 是否自动保活(每调用一次接口就增加时间)

        # 3.检测是否配置了自动销毁
        if settings.server_timeout_minutes is not None:
            # 4.设置销毁时间+定时器
            self.shutdown_time = datetime.now(UTC) + timedelta(
                minutes=settings.server_timeout_minutes
            )
            self._setup_timer(settings.server_timeout_minutes)

    @property
    def expand_enabled(self) -> bool:
        """只读属性，返回是否自动保活"""
        return self._expand_enabled

    def disable_expand(self) -> None:
        """关闭自动保活"""
        self._expand_enabled = False

    def _setup_timer(self, minutes: int) -> None:
        """传递时间(分钟)并创建定时器，在时间结束之后关闭supervisord主进程"""
        # 1.检测当前是否存在销毁任务，如果存在则先取消
        if self.shutdown_task:
            try:
                self.shutdown_task.cancel()
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning("取消shutdown任务失败: %s", e)

        # 2.创建一个异步定时器任务函数
        async def shutdown_after_timeout():
            await asyncio.sleep(minutes * 60)
            await self.shutdown()

        try:
            # 3.获取事件循环并添加任务
            loop = asyncio.get_event_loop()
            self.shutdown_task = loop.create_task(shutdown_after_timeout())
        except (OSError, RuntimeError, ValueError) as _:
            # 4.如果事件循环失败则创建一个新的线程来执行定时器
            if hasattr(self, "shutdown_timer") and self.shutdown_timer:
                self.shutdown_timer.cancel()

            # 5.使用线程创建关闭定时器并设置在后台运行
            self.shutdown_timer = threading.Timer(
                minutes * 60, lambda: asyncio.run(self.shutdown())
            )
            self.shutdown_timer.daemon = True
            self.shutdown_timer.start()

    def _connect_rpc(self) -> None:
        """使用python的xml-rpc客户端连接一个本地sock文件文件实现连接rpc服务"""
        try:
            self.server = xmlrpc.client.ServerProxy(
                "http://localhost",
                transport=UnixStreamTransport(self.rpc_url),
            )
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("连接Supervisor服务失败: %s", e)
            raise BadRequestException(f"连接Supervisor服务失败: {e!s}") from e

    async def _call_rpc(self, method_name: str, *args) -> Any:
        """根据传递的方法名(如'supervisor.getAllProcessInfo')+参数调用rpc方法。

        惰性连接：首次调用时才真正建立到 supervisor.sock 的RPC连接，
        使得没有该socket文件的环境（如CI）也能实例化SupervisorService；
        连不上时行为不变，仍抛出BadRequestException，只是触发时点从构造函数
        挪到了首次调用本方法。
        """
        try:
            if self.server is None:
                self._connect_rpc()

            method = self.server
            for part in method_name.split("."):
                method = getattr(method, part)

            return await asyncio.to_thread(method, *args)
        except BadRequestException:
            raise
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("RPC方法调用失败: %s", e)
            raise BadRequestException(f"RPC方法调用失败: {e!s}") from e

    async def get_all_processes(self) -> list[ProcessInfo]:
        """获取当前supervisor管理的所有进程信息"""
        try:
            processes = await self._call_rpc("supervisor.getAllProcessInfo")
            return [ProcessInfo(**process) for process in processes]
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("获取进程信息失败: %s", e)
            raise AppException(f"获取进程信息失败: {e!s}") from e

    async def stop_all_processes(self) -> SupervisorActionResult:
        """停止supervisor管理的所有进程"""
        try:
            result = await self._call_rpc("supervisor.stopAllProcesses")
            return SupervisorActionResult(status="stopped", result=result)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("停止supervisor所有进程服务失败: %s", e)
            raise AppException(f"停止supervisor所有进程服务失败: {e!s}") from e

    async def shutdown(self) -> SupervisorActionResult:
        """关闭supervisord服务"""
        try:
            shutdown_result = await self._call_rpc("supervisor.shutdown")
            return SupervisorActionResult(status="shutdown", shutdown_result=shutdown_result)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("关闭supervisord服务失败: %s", e)
            raise AppException(f"关闭supervisord服务失败: {e!s}") from e

    async def restart(self) -> SupervisorActionResult:
        """重启Supervisor管理的进程"""
        try:
            stop_result = await self._call_rpc("supervisor.stopAllProcesses")
            start_result = await self._call_rpc("supervisor.startAllProcesses")
            return SupervisorActionResult(
                status="restarted",
                stop_result=stop_result,
                start_result=start_result,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("重启Supervisor进程服务失败")
            raise AppException("重启Supervisor进程服务失败") from exc

    async def activate_timeout(self, minutes: int | None = None) -> SupervisorTimeout:
        """传递指定分钟，并激活定时销毁任务同时关闭自动保活"""
        # 1.获取超时分钟数
        setting = get_settings()
        timeout_minutes = minutes or setting.server_timeout_minutes
        if timeout_minutes is None:
            raise BadRequestException("超时时间未配置, 并且未读取到系统默认超时时间")

        # 2.更新超时配置
        self.timeout_active = True
        self.shutdown_time = datetime.now(UTC) + timedelta(minutes=timeout_minutes)

        # 3.创建一个新的定时器
        self._setup_timer(timeout_minutes)

        return SupervisorTimeout(
            status="timeout_activated",
            active=True,
            shutdown_time=self.shutdown_time.isoformat(),
            timeout_minutes=timeout_minutes,
            remaining_seconds=(self.shutdown_time - datetime.now(UTC)).total_seconds(),
        )

    async def extend_timeout(self, minutes: int | None = 3) -> SupervisorTimeout:
        """传递指定的时长，延长超时销毁的时间，单默认延长3分钟"""
        # 1.获取超时分钟数
        if minutes is None:
            raise BadRequestException("超时时间未配置, 请核实后重试")
        remaining = self.shutdown_time - datetime.now(UTC)
        timeout_minutes = round(max(0, remaining.total_seconds()) / 60) + minutes

        # 2.更新超时配置
        self.timeout_active = True
        self.shutdown_time = datetime.now(UTC) + timedelta(minutes=timeout_minutes)

        # 3.创建一个新的定时器
        self._setup_timer(timeout_minutes)

        return SupervisorTimeout(
            status="timeout_extended",
            active=True,
            shutdown_time=self.shutdown_time.isoformat(),
            timeout_minutes=timeout_minutes,
            remaining_seconds=(self.shutdown_time - datetime.now(UTC)).total_seconds(),
        )

    async def cancel_timeout(self) -> SupervisorTimeout:
        """取消超时销毁设置"""
        # 1.判断是否设置了超时销毁
        if not self.timeout_active:
            return SupervisorTimeout(status="no_timeout_active", active=False)

        # 2.取消销毁任务
        if self.shutdown_task:
            try:
                self.shutdown_task.cancel()
                self.shutdown_task = None
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning("取消shutdown任务失败: %s", e)

        # 3.同步检查是否存在定时器
        if hasattr(self, "shutdown_timer") and self.shutdown_timer:
            self.shutdown_timer.cancel()
            self.shutdown_timer = None

        # 4.更新超时配置
        self.timeout_active = False
        self.shutdown_time = None
        self._expand_enabled = True

        return SupervisorTimeout(status="timeout_cancelled", active=False)

    async def get_timeout_status(self) -> SupervisorTimeout:
        """获取当前supervisor的超时状态"""
        # 1.判断是否开启超时销毁功能
        if not self.timeout_active:
            return SupervisorTimeout(active=False)

        # 2.统计剩余秒数
        remaining_seconds = 0
        if self.shutdown_time:
            remaining = self.shutdown_time - datetime.now(UTC)
            remaining_seconds = max(0, remaining.total_seconds())

        return SupervisorTimeout(
            active=self.timeout_active,
            shutdown_time=self.shutdown_time.isoformat() if self.shutdown_time else None,
            remaining_seconds=remaining_seconds,
        )

    async def stop_process(self, name: str) -> SupervisorActionResult:
        """Stop a single supervisor-managed process."""
        try:
            result = await self._call_rpc("supervisor.stopProcess", name)
            return SupervisorActionResult(status="stopped", result=result, process=name)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("停止进程[%s]失败: %s", name, e)
            raise AppException(f"停止进程[{name}]失败: {e!s}") from e

    async def start_process(self, name: str) -> SupervisorActionResult:
        """Start a single supervisor-managed process."""
        try:
            result = await self._call_rpc("supervisor.startProcess", name)
            return SupervisorActionResult(status="started", result=result, process=name)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("启动进程[%s]失败: %s", name, e)
            raise AppException(f"启动进程[{name}]失败: {e!s}") from e

    async def restart_chrome(self) -> SupervisorActionResult:
        """Restart only the Chrome browser process."""
        stop_result = await self.stop_process("chrome")
        start_result = await self.start_process("chrome")
        return SupervisorActionResult(
            status="restarted",
            stop_result=stop_result.result,
            start_result=start_result.result,
            process="chrome",
        )
