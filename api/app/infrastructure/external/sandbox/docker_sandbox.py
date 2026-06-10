#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import io
import logging
import socket
import time
import uuid
from datetime import datetime
from typing import Optional, Self, BinaryIO

import docker
import httpx
from async_lru import alru_cache
from docker.errors import NotFound, APIError
from docker.models.resource import Model

from app.domain.external.browser import Browser
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser
from app.application.services.config_provider import get_runtime_config
from core.config import get_settings

logger = logging.getLogger(__name__)

_sync_redis_client = None


def _get_sync_redis_client():
    global _sync_redis_client
    if _sync_redis_client is None:
        import redis as sync_redis

        settings = get_settings()
        _sync_redis_client = sync_redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            decode_responses=True,
        )
    return _sync_redis_client


class DockerSandbox(Sandbox):
    """基于Docker的沙箱服务"""

    def __init__(
            self,
            ip: Optional[str] = None,
            container_name: Optional[str] = None
    ) -> None:
        """构造函数，完成Docker沙箱扩展创建"""
        self.client = httpx.AsyncClient(timeout=600)
        self._ip = ip
        self._container_name = container_name
        self._base_url = f"http://{ip}:8080"
        self._vnc_url = f"ws://{ip}:5901"
        self._cdp_url = f"http://{ip}:9222"

    @property
    def id(self) -> str:
        """获取沙箱的唯一id，使用容器名字作为唯一id"""
        if not self._container_name:
            return "my-manus-sandbox"
        return self._container_name

    @property
    def vnc_url(self) -> str:
        return self._vnc_url

    @property
    def cdp_url(self) -> str:
        return self._cdp_url

    @classmethod
    @alru_cache(maxsize=128, typed=True)
    async def _resolve_hostname_to_ip(cls, hostname: str) -> Optional[str]:
        """将docker容器主机/地址转换成ipv4格式数据"""
        try:
            # 1.首先解析传递的hostname是不是ip
            try:
                socket.inet_pton(socket.AF_INET, hostname)
                return hostname
            except OSError:
                pass

            # 2.使用socket获取地址信息
            addr_info = socket.getaddrinfo(hostname, None, family=socket.AF_INET)

            # 3.判断地址信息是否存在，如果存在则返回第一个ipv4地址
            if addr_info and len(addr_info) > 0:
                return addr_info[0][4][0]

            return None
        except Exception as e:
            logger.error(f"解析Docker容器主机地址{hostname}失败: {str(e)}")
            return None

    @classmethod
    def _get_container_ip(cls, container: Model) -> str:
        """根据传递的容器获取ip信息"""
        # 1.获取inspect网络设置
        network_settings = container.attrs["NetworkSettings"]
        ip_address = network_settings["IPAddress"]

        # 2.判断容器是否存在ip，如果不存在则从networks中获取
        if not ip_address and "Networks" in network_settings:
            networks = network_settings["Networks"]
            # 3.循环遍历每一项网络配置
            for network_name, network_config in networks.items():
                if "IPAddress" in network_config and network_config["IPAddress"]:
                    ip_address = network_config["IPAddress"]
                    break

        return ip_address

    @classmethod
    def _create_task(cls) -> Self:
        """创建沙箱容器的异步任务"""
        # 1.构建容器的名字
        image = get_runtime_config().sandbox.image
        name_prefix = get_runtime_config().sandbox.name_prefix
        container_name = f"{name_prefix}-{str(uuid.uuid4())[:8]}"

        try:
            # 3.创建一个docker客户端
            docker_client = docker.from_env()

            # 4.预配置容器信息
            container_config = {
                "image": image,
                "name": container_name,
                "detach": True,
                "remove": True,
                "environment": {
                    "SERVER_TIMEOUT_MINUTES": str(get_runtime_config().sandbox.ttl_minutes or 60),
                    "CHROME_ARGS": get_runtime_config().sandbox.chrome_args or "",
                    "HTTPS_PROXY": get_runtime_config().sandbox.https_proxy or "",
                    "HTTP_PROXY": get_runtime_config().sandbox.http_proxy or "",
                    "NO_PROXY": get_runtime_config().sandbox.no_proxy or "",
                }
            }
            if get_runtime_config().sandbox.memory_limit:
                container_config["mem_limit"] = get_runtime_config().sandbox.memory_limit
            if get_runtime_config().sandbox.cpu_limit and get_runtime_config().sandbox.cpu_limit > 0:
                container_config["nano_cpus"] = int(get_runtime_config().sandbox.cpu_limit * 1_000_000_000)
            if get_runtime_config().sandbox.pids_limit and get_runtime_config().sandbox.pids_limit > 0:
                container_config["pids_limit"] = get_runtime_config().sandbox.pids_limit

            # 5.判断是否传递了网络
            if get_runtime_config().sandbox.network:
                container_config["network"] = get_runtime_config().sandbox.network

            # 6.调用docker客户端容器运行参数创建沙箱
            container = docker_client.containers.run(**container_config)

            # 7.重载并刷新容器信息
            container.reload()
            ip = cls._get_container_ip(container)

            return DockerSandbox(ip=ip, container_name=container_name)
        except Exception as e:
            logger.error(f"创建Docker沙箱容器失败: {str(e)}")
            raise Exception(f"创建Docker沙箱容器失败: {str(e)}")

    @classmethod
    async def _create_and_warm(cls) -> Self:
        """Create a sandbox container and wait until supervisor services are ready."""
        sandbox = await cls._create_fresh()
        await sandbox.ensure_sandbox()
        from app.infrastructure.external.sandbox.sandbox_pool import SandboxPool

        await SandboxPool.touch_activity(sandbox.id)
        return sandbox

    @classmethod
    async def _create_fresh(cls) -> Self:
        if get_runtime_config().sandbox.address:
            ip = await cls._resolve_hostname_to_ip(get_runtime_config().sandbox.address)
            return DockerSandbox(ip=ip)
        return await asyncio.to_thread(cls._create_task)

    @classmethod
    async def create(cls) -> Self:
        """类方法，创建沙箱容器（优先从预热池获取）"""
        if get_runtime_config().sandbox.address:
            ip = await cls._resolve_hostname_to_ip(get_runtime_config().sandbox.address)
            return DockerSandbox(ip=ip)

        from app.infrastructure.external.sandbox.sandbox_pool import get_sandbox_pool

        pool = get_sandbox_pool()
        if pool.enabled:
            return await pool.acquire()
        return await cls._create_and_warm()

    async def destroy(self) -> bool:
        """销毁当前的DockerSandbox实例"""
        try:
            # 1.关闭httpx客户端
            if self.client:
                await self.client.aclose()

            # 2.关闭并移除容器
            if self._container_name:
                await asyncio.to_thread(self._remove_container, self._container_name)
            return True
        except Exception as e:
            logger.error(f"销毁当前Docker沙箱[{self._container_name}]失败: {str(e)}")
            return False

    @classmethod
    def _remove_container(cls, container_name: str) -> None:
        docker_client = docker.from_env()
        try:
            docker_client.containers.get(container_name).remove(force=True)
        finally:
            docker_client.close()

    @classmethod
    def _get_running_container_ip(cls, id: str) -> Optional[str]:
        docker_client = docker.from_env()
        try:
            container = docker_client.containers.get(id)
            container.reload()
            if container.status != "running":
                logger.warning(f"容器存在但未运行, 容器名字: {id}")
                return None
            return cls._get_container_ip(container)
        except NotFound:
            logger.warning(f"该容器找不到可能被销毁: {str(id)}")
            return None
        except APIError as e:
            logger.error(f"Docker API出错: {str(e)}")
            return None
        finally:
            docker_client.close()

    @classmethod
    def _cleanup_orphaned_containers_sync(cls) -> int:
        if get_runtime_config().sandbox.address or not get_runtime_config().sandbox.name_prefix:
            return 0
        docker_client = docker.from_env()
        removed = 0
        idle_timeout_seconds = max(60, (get_runtime_config().sandbox.idle_timeout_minutes or 30) * 60)
        now = time.time()
        try:
            containers = docker_client.containers.list(
                all=True,
                filters={"name": f"{get_runtime_config().sandbox.name_prefix}-"},
            )
            for container in containers:
                container.reload()
                if container.status in {"exited", "dead", "created"}:
                    container.remove(force=True)
                    removed += 1
                    continue
                if container.status != "running":
                    continue
                container_name = container.name.lstrip("/")
                started_at = container.attrs.get("State", {}).get("StartedAt")
                idle_seconds = idle_timeout_seconds
                if started_at:
                    try:
                        started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        idle_seconds = now - started_dt.timestamp()
                    except ValueError:
                        idle_seconds = 0
                if idle_seconds < idle_timeout_seconds:
                    continue
                try:
                    redis_client = _get_sync_redis_client()
                    last_active_raw = redis_client.get(f"sandbox:last_active:{container_name}")
                    if last_active_raw:
                        last_active = int(last_active_raw)
                        if now - last_active < idle_timeout_seconds:
                            continue
                    else:
                        logger.debug(
                            "Skip idle cleanup for %s: no Redis activity key",
                            container_name,
                        )
                        continue
                except Exception as exc:
                    logger.warning(
                        "Redis unavailable, skip idle cleanup for running sandbox %s: %s",
                        container_name,
                        exc,
                    )
                    continue
                try:
                    container.stop(timeout=10)
                except Exception:
                    pass
                try:
                    container.remove(force=True)
                    removed += 1
                    logger.info("Removed idle sandbox container: %s", container_name)
                except Exception as exc:
                    logger.warning("Failed to remove idle sandbox %s: %s", container_name, exc)
            return removed
        finally:
            docker_client.close()

    @classmethod
    async def cleanup_orphaned_containers(cls) -> int:
        return await asyncio.to_thread(cls._cleanup_orphaned_containers_sync)

    @classmethod
    async def get(cls, id: str) -> Optional[Self]:
        """根据传递的id获取沙箱实例"""
        # 1.判断是否直连沙箱
        if get_runtime_config().sandbox.address:
            try:
                ip = await cls._resolve_hostname_to_ip(get_runtime_config().sandbox.address)
                return DockerSandbox(ip=ip, container_name=id)
            except Exception as e:
                logger.error(f"解析沙箱地址失败: {str(e)}")
                return None

        try:
            # 2.创建docker客户端并根据容器名字获取容器（在线程中执行同步 Docker SDK）
            ip = await asyncio.to_thread(cls._get_running_container_ip, id)
            if not ip:
                return None
            return DockerSandbox(ip=ip, container_name=id)
        except Exception as e:
            # 8.其他错误统一捕获
            logger.error(f"获取沙箱发生未知错误: {str(e)}")
            return None

    async def get_browser(self, supports_multimodal: bool = False) -> Browser:
        """获取沙箱中的浏览器实例"""
        return PlaywrightBrowser(self.cdp_url, supports_multimodal=supports_multimodal)

    async def ensure_sandbox(self) -> None:
        """确保沙箱一定存在/服务全部都开启了才执行后续步骤"""
        max_retries = 30
        retry_interval = max(0.5, get_runtime_config().sandbox.warmup_retry_interval_seconds)

        # 2.循环请求获取supervisor状态并判断服务是否正常
        for attempt in range(max_retries):
            try:
                # 3.调用client客户端向沙箱发起api请求获取状态
                response = await self.client.get(f"{self._base_url}/api/supervisor/status")
                response.raise_for_status()

                # 4.将响应结果转换为ToolResult
                tool_result = ToolResult.from_sandbox(**response.json())

                # 5.判断是否执行成功
                if not tool_result.success:
                    logger.warning(f"Supervisor进程状态监测失败: {tool_result.message}")
                    await asyncio.sleep(retry_interval)
                    continue

                # 6.读取services数据并判断
                services = tool_result.data or []
                if not services:
                    logger.warning(f"Supervisor进程中未发现任何服务")
                    await asyncio.sleep(retry_interval)
                    continue

                # 7.循环遍历所有服务并判断是否全部正常运行
                all_running = True
                non_running_services = []
                for service in services:
                    service_name = service.get("name", "unknown")
                    state_name = service.get("statename", "")

                    # 8.判断state_name是不是RUNNING
                    if state_name != "RUNNING":
                        all_running = False
                        non_running_services.append(f"{service_name}({state_name})")

                # 9.判断是否所有服务都启动
                if all_running:
                    logger.info("Sandbox Supervisor所有进程服务运行正常")
                    from app.infrastructure.external.sandbox.sandbox_pool import SandboxPool

                    await SandboxPool.touch_activity(self.id)
                    return
                else:
                    logger.info(f"正在等待Sandbox Supervisor进程服务运行, 还未运行的服务列表: {non_running_services}")
                    await asyncio.sleep(retry_interval)
            except Exception as e:
                logger.warning(f"无法确认Sandbox Supervisor进程状态: {str(e)}")
                await asyncio.sleep(retry_interval)

        # 经过max_retries次监测后还无法确认则抛出异常
        logger.error(f"在经过{max_retries}次尝试后仍无法确认Sandbox Supervisor状态信息")
        raise Exception(f"在经过{max_retries}次尝试后仍无法确认Sandbox Supervisor状态信息")

    async def read_file(
            self,
            filepath: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
            sudo: bool = False,
            max_length: int = 10000
    ) -> ToolResult:
        """读取沙箱中指定路径的文件内容"""
        response = await self.client.post(
            f"{self._base_url}/api/file/read-file",
            json={
                "filepath": filepath,
                "start_line": start_line,
                "end_line": end_line,
                "sudo": sudo,
                "max_length": max_length,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def write_file(
            self,
            filepath: str,
            content: str,
            append: bool = False,
            leading_newline: bool = False,
            trailing_newline: bool = False,
            sudo: bool = False,
    ) -> ToolResult:
        """向沙箱中指定文件写入内容"""
        response = await self.client.post(
            f"{self._base_url}/api/file/write-file",
            json={
                "filepath": filepath,
                "content": content,
                "append": append,
                "leading_newline": leading_newline,
                "trailing_newline": trailing_newline,
                "sudo": sudo,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def replace_in_file(
            self,
            filepath: str,
            old_str: str,
            new_str: str,
            sudo: bool = False,
    ) -> ToolResult:
        """替换沙箱中文件的旧内容为指定内容"""
        response = await self.client.post(
            f"{self._base_url}/api/file/replace-in-file",
            json={
                "filepath": filepath,
                "old_str": old_str,
                "new_str": new_str,
                "sudo": sudo,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def search_in_file(self, filepath: str, regex: str, sudo: bool = False) -> ToolResult:
        """搜索沙箱中指定文件的内容"""
        response = await self.client.post(
            f"{self._base_url}/api/file/search-in-file",
            json={
                "filepath": filepath,
                "regex": regex,
                "sudo": sudo,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def find_files(self, dir_path: str, glob_pattern: str) -> ToolResult:
        """查找沙箱中指定目录的文件列表"""
        response = await self.client.post(
            f"{self._base_url}/api/file/find-files",
            json={
                "dir_path": dir_path,
                "glob_pattern": glob_pattern,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def list_files(self, dir_path: str) -> ToolResult:
        """传递目录列出沙箱指定目录下的所有文件"""
        return await self.find_files(dir_path, "*")

    async def check_file_exists(self, filepath: str) -> ToolResult:
        """传递指定路径检查沙箱中指定文件是否存在"""
        response = await self.client.post(
            f"{self._base_url}/api/file/check-file-exists",
            json={
                "filepath": filepath,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def delete_file(self, filepath: str) -> ToolResult:
        """传递路径删除指定的文件"""
        response = await self.client.post(
            f"{self._base_url}/api/file/delete-file",
            json={
                "filepath": filepath,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def upload_file(
            self,
            file_data: BinaryIO,
            filepath: str,
            filename: str = None,
    ) -> ToolResult:
        """将文件源上传至沙箱指定位置"""
        # 1.预配置上传数据
        files = {"file": (filename or "upload", file_data, "application/octet-stream")}
        data = {"filepath": filepath}

        # 2.发起请求上传数据获取响应
        response = await self.client.post(
            f"{self._base_url}/api/file/upload-file",
            files=files,
            data=data,
        )
        return ToolResult.from_sandbox(**response.json())

    async def download_file(self, filepath: str) -> BinaryIO:
        """从沙箱中下载文件"""
        response = await self.client.get(
            f"{self._base_url}/api/file/download-file",
            params={"filepath": filepath}
        )
        response.raise_for_status()

        return io.BytesIO(response.content)

    async def exec_command(self, session_id: str, exec_dir: str, command: str) -> ToolResult:
        """在沙箱中执行命令"""
        response = await self.client.post(
            f"{self._base_url}/api/shell/exec-command",
            json={
                "session_id": session_id,
                "exec_dir": exec_dir,
                "command": command,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def read_shell_output(self, session_id: str, console: bool = False) -> ToolResult:
        """读取沙箱中shell的输出"""
        response = await self.client.post(
            f"{self._base_url}/api/shell/read-shell-output",
            json={
                "session_id": session_id,
                "console": console,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def write_shell_input(
            self,
            session_id: str,
            input_text: str,
            press_enter: bool = True,
    ) -> ToolResult:
        """向沙箱的Shell进程写入数据"""
        response = await self.client.post(
            f"{self._base_url}/api/shell/write-shell-input",
            json={
                "session_id": session_id,
                "input_text": input_text,
                "press_enter": press_enter,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def wait_process(self, session_id: str, seconds: Optional[int] = None) -> ToolResult:
        """等待沙箱中进程的执行"""
        response = await self.client.post(
            f"{self._base_url}/api/shell/wait-process",
            json={
                "session_id": session_id,
                "seconds": seconds,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    async def kill_process(self, session_id: str) -> ToolResult:
        """杀死沙箱中指定进程"""
        response = await self.client.post(
            f"{self._base_url}/api/shell/kill-process",
            json={
                "session_id": session_id,
            }
        )
        return ToolResult.from_sandbox(**response.json())

    _CHECKPOINT_SHELL_SESSION = "checkpoint"

    async def create_workspace_snapshot(self, snapshot_id: str) -> bytes:
        """Create a tar.gz snapshot of /home/ubuntu and return its bytes."""
        archive_path = f"/tmp/cp_{snapshot_id}.tgz"
        create_cmd = (
            f"tar czf {archive_path} -C /home/ubuntu "
            f"--exclude='.snapshots' --exclude='*.tgz' ."
        )
        result = await self.exec_command(
            self._CHECKPOINT_SHELL_SESSION,
            "/home/ubuntu",
            create_cmd,
        )
        if not result.success:
            raise RuntimeError(f"创建沙箱快照失败: {result.message or result.data}")

        try:
            stream = await self.download_file(archive_path)
            return stream.read()
        finally:
            await self.exec_command(
                self._CHECKPOINT_SHELL_SESSION,
                "/home/ubuntu",
                f"rm -f {archive_path}",
            )

    async def restore_workspace_snapshot(self, snapshot_id: str, snapshot_data: BinaryIO) -> None:
        """Restore /home/ubuntu from a tar.gz snapshot."""
        archive_path = f"/tmp/cp_restore_{snapshot_id}.tgz"
        upload_result = await self.upload_file(
            file_data=snapshot_data,
            filepath=archive_path,
            filename=f"cp_restore_{snapshot_id}.tgz",
        )
        if not upload_result.success:
            raise RuntimeError(f"上传沙箱快照失败: {upload_result.message or upload_result.data}")

        restore_cmd = (
            f"find /home/ubuntu -mindepth 1 -maxdepth 1 "
            f"! -name '.snapshots' -exec rm -rf {{}} + && "
            f"tar xzf {archive_path} -C /home/ubuntu && rm -f {archive_path}"
        )
        result = await self.exec_command(
            self._CHECKPOINT_SHELL_SESSION,
            "/home/ubuntu",
            restore_cmd,
        )
        if not result.success:
            raise RuntimeError(f"恢复沙箱快照失败: {result.message or result.data}")
