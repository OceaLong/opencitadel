import asyncio
import contextlib
import io
import logging
import secrets
import socket
import time
import uuid
from datetime import datetime
from typing import BinaryIO, Self
from urllib.parse import quote

import docker
import httpx
from async_lru import alru_cache
from docker.errors import APIError, NotFound
from docker.models.resource import Model

from app.domain.external.browser import Browser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser
from app.infrastructure.external.sandbox.admission import SandboxQuota
from app.infrastructure.external.sandbox.sandbox_container_policy import (
    CreateSandboxRequest,
    SandboxContainerPolicy,
    build_docker_sandbox_config,
)
from app.infrastructure.external.sandbox.settings import (
    SandboxEffectiveSettings,
    SandboxHostAccess,
)

logger = logging.getLogger(__name__)


class DockerSandboxError(RuntimeError):
    """Raised when a Docker-backed sandbox cannot be created or warmed."""


def _broker_url(host: SandboxHostAccess) -> str:
    return (host.broker_url or "").rstrip("/")


def _broker_call(host: SandboxHostAccess, method: str, path: str, **kwargs) -> dict:
    url = _broker_url(host)
    if not url:
        raise RuntimeError("sandbox broker is not configured")
    try:
        with httpx.Client(
            headers={"Authorization": f"Bearer {host.broker_token or ''}"},
            timeout=30,
            trust_env=False,
        ) as client:
            response = client.request(method, f"{url}{path}", **kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            detail = str(exc.response.json().get("detail") or "broker rejected request")
        except (TypeError, ValueError):
            detail = "broker rejected request"
        detail = " ".join(detail.split())[:256]
        raise DockerSandboxError(
            f"sandbox broker {method} {path} failed with HTTP {exc.response.status_code}: {detail}"
        ) from exc
    except httpx.RequestError as exc:
        raise DockerSandboxError(f"sandbox broker {method} {path} is unavailable") from exc
    return response.json()


def _broker_list(host: SandboxHostAccess) -> list[dict]:
    return list(_broker_call(host, "GET", "/v1/sandboxes").get("sandboxes") or [])


def _broker_create(
    host: SandboxHostAccess,
    container_name: str,
    settings: SandboxEffectiveSettings,
    access_token: str,
) -> dict:
    request = CreateSandboxRequest(
        id=container_name,
        operations_revision_id=settings.operations_revision_id,
        policy=SandboxContainerPolicy.from_operations(settings.policy),
        access_token=access_token,
    )
    return _broker_call(
        host,
        "POST",
        "/v1/sandboxes",
        json=request.model_dump(mode="json"),
    )


def _broker_sandbox_path(container_name: str) -> str:
    """Encode an opaque sandbox id as exactly one broker path segment."""
    if not container_name:
        raise ValueError("sandbox id must not be empty")
    return f"/v1/sandboxes/{quote(container_name, safe='')}"


def _get_docker_client(host: SandboxHostAccess):
    if host.environment == "production":
        raise RuntimeError(
            "direct Docker access is disabled in production; configure "
            "SANDBOX_BROKER_URL or use the Kubernetes sandbox driver"
        )
    return docker.from_env()


def _get_sync_redis_client(host: SandboxHostAccess):
    import redis as sync_redis

    return sync_redis.Redis(
        host=host.redis_host,
        port=host.redis_port,
        db=host.redis_db,
        password=host.redis_password,
        decode_responses=True,
    )


class DockerSandbox(Sandbox):
    """基于Docker的沙箱服务"""

    def __init__(
        self,
        *,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
        quota: SandboxQuota,
        ip: str | None = None,
        container_name: str | None = None,
        access_token: str = "",
    ) -> None:
        """构造函数，完成Docker沙箱扩展创建"""
        self.client = httpx.AsyncClient(
            timeout=600,
            headers=({"Authorization": f"Bearer {access_token}"} if access_token else {}),
        )
        self._ip = ip
        self._container_name = container_name
        self.settings = settings
        self._host = host
        self._quota = quota
        self._base_url = f"http://{ip}:8080"
        self._vnc_url = f"ws://{ip}:5901"
        self._cdp_url = f"http://{ip}:9222"

    @property
    def id(self) -> str:
        """获取沙箱的唯一id，使用容器名字作为唯一id"""
        if not self._container_name:
            return "opencitadel-sandbox"
        return self._container_name

    @property
    def vnc_url(self) -> str:
        return self._vnc_url

    @property
    def cdp_url(self) -> str:
        return self._cdp_url

    @classmethod
    @alru_cache(maxsize=128, typed=True)
    async def _resolve_hostname_to_ip(cls, hostname: str) -> str | None:
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
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("解析Docker容器主机地址%s失败: %s", hostname, e)
            return None

    @staticmethod
    def _ipv4_from_endpoint(endpoint: dict) -> str | None:
        ip = (endpoint.get("IPAddress") or "").strip()
        return ip or None

    @classmethod
    def _get_container_ip(
        cls,
        container: Model,
        preferred_network: str | None = None,
    ) -> str | None:
        """根据传递的容器获取 IPv4 地址（兼容自定义 bridge 网络）。"""
        network_settings = container.attrs.get("NetworkSettings") or {}
        networks = network_settings.get("Networks") or {}

        if preferred_network:
            endpoint = networks.get(preferred_network)
            if endpoint:
                ip = cls._ipv4_from_endpoint(endpoint)
                if ip:
                    return ip

        ip = cls._ipv4_from_endpoint(network_settings)
        if ip:
            return ip

        for endpoint in networks.values():
            ip = cls._ipv4_from_endpoint(endpoint)
            if ip:
                return ip

        return None

    @classmethod
    def _require_container_ip(
        cls,
        container: Model,
        container_name: str,
        preferred_network: str | None = None,
    ) -> str:
        ip = cls._get_container_ip(container, preferred_network=preferred_network)
        if ip:
            return ip
        network_label = preferred_network or "default"
        raise RuntimeError(f"沙箱[{container_name}]在网络[{network_label}]上未分配到 IPv4 地址")

    @classmethod
    def _list_live_sandbox_ids_sync(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
    ) -> set[str]:
        deployment = settings.deployment
        if deployment.address or not deployment.name_prefix:
            return set()
        if _broker_url(host):
            return {item["id"] for item in _broker_list(host) if item.get("status") == "running"}
        containers = _get_docker_client(host).containers.list(
            filters={
                "name": f"{deployment.name_prefix}-",
                "status": "running",
            },
        )
        return {container.name.lstrip("/") for container in containers}

    @classmethod
    async def list_live_sandbox_ids(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
    ) -> set[str]:
        return await asyncio.to_thread(cls._list_live_sandbox_ids_sync, settings, host)

    @classmethod
    def _create_task_with_name(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
        quota: SandboxQuota,
        container_name: str,
    ) -> Self:
        deployment = settings.deployment
        container: Model | None = None
        # Per-sandbox bearer token for the data-plane HTTP API. Injected into the
        # container env and sent on every kernel->sandbox request. The sandbox
        # only enforces it when the env var is present (backward compatible).
        access_token = secrets.token_urlsafe(32)
        try:
            if _broker_url(host):
                payload = _broker_create(host, container_name, settings, access_token)
                return cls(
                    settings=settings,
                    host=host,
                    quota=quota,
                    ip=payload["ip"],
                    container_name=payload["id"],
                    access_token=access_token,
                )
            container = _get_docker_client(host).containers.run(
                **build_docker_sandbox_config(
                    deployment,
                    SandboxContainerPolicy.from_operations(settings.policy),
                    container_name,
                    operations_revision_id=settings.operations_revision_id,
                    access_token=access_token,
                )
            )
            container.reload()
            ip = cls._require_container_ip(
                container,
                container_name,
                preferred_network=deployment.network,
            )
            return cls(
                settings=settings,
                host=host,
                quota=quota,
                ip=ip,
                container_name=container_name,
                access_token=access_token,
            )
        except BaseException as exc:
            if container is not None:
                with contextlib.suppress(Exception):
                    container.remove(force=True)
            if isinstance(exc, (OSError, RuntimeError, ValueError)):
                logger.error("创建Docker沙箱容器失败: %s", exc)
                raise DockerSandboxError(f"创建Docker沙箱容器失败: {exc!s}") from exc
            raise

    @classmethod
    async def create_and_warm(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
        quota: SandboxQuota,
        *,
        max_retries: int | None = None,
    ) -> Self:
        deployment = settings.deployment
        if deployment.address:
            ip = await cls._resolve_hostname_to_ip(deployment.address)
            return cls(settings=settings, host=host, quota=quota, ip=ip)
        if not deployment.name_prefix:
            raise RuntimeError("sandbox name prefix is not configured")
        container_name = f"{deployment.name_prefix}-{str(uuid.uuid4())[:8]}"
        if not await quota.acquire(container_name, settings.policy):
            raise RuntimeError("沙箱准入未通过：节点配额或内存水位不足")
        sandbox: Self | None = None
        try:
            sandbox = await asyncio.to_thread(
                cls._create_task_with_name,
                settings,
                host,
                quota,
                container_name,
            )
            await sandbox.ensure_sandbox(max_retries=max_retries)
        except BaseException:
            try:
                if sandbox is None:
                    await quota.release(container_name)
                else:
                    # Once creation succeeds, the instance owns both the
                    # container and quota lease. Destroy it on every warmup
                    # failure, including cancellation, so a failed prewarm
                    # cannot leak a live but unusable sandbox.
                    await asyncio.shield(sandbox.destroy())
            except Exception:
                logger.exception("Failed to compensate sandbox warmup failure: %s", container_name)
            raise
        return sandbox

    async def destroy(self) -> bool:
        """销毁当前的DockerSandbox实例"""
        holder_id = self._container_name
        destroyed = True
        try:
            # 1.关闭httpx客户端
            if self.client:
                await self.client.aclose()

            # 2.关闭并移除容器
            if self._container_name:
                await asyncio.to_thread(
                    self._remove_container,
                    self._host,
                    self._container_name,
                )
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("销毁当前Docker沙箱[%s]失败: %s", self._container_name, e)
            destroyed = False
        finally:
            if holder_id:
                await self._quota.release(holder_id)
        return destroyed

    @classmethod
    def _remove_container(cls, host: SandboxHostAccess, container_name: str) -> None:
        if _broker_url(host):
            _broker_call(host, "DELETE", _broker_sandbox_path(container_name))
            return
        _get_docker_client(host).containers.get(container_name).remove(force=True)

    @classmethod
    def _get_running_container_ip(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
        sandbox_id: str,
    ) -> str | None:
        if _broker_url(host):
            try:
                payload = _broker_call(host, "GET", _broker_sandbox_path(sandbox_id))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None
                raise
            if payload.get("status") != "running":
                return None
            return payload.get("ip") or None
        try:
            container = _get_docker_client(host).containers.get(sandbox_id)
            container.reload()
            if container.status != "running":
                logger.warning("容器存在但未运行, 容器名字: %s", sandbox_id)
                return None
            return cls._get_container_ip(
                container,
                preferred_network=settings.deployment.network,
            )
        except NotFound:
            logger.warning("该容器找不到可能被销毁: %s", sandbox_id)
            return None
        except APIError as e:
            logger.error("Docker API出错: %s", e)
            return None

    @classmethod
    def _cleanup_orphaned_containers_sync(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
    ) -> int:
        deployment = settings.deployment
        if deployment.address or not deployment.name_prefix:
            return 0
        if _broker_url(host):
            return cls._cleanup_broker_sandboxes_sync(settings, host)
        docker_client = _get_docker_client(host)
        removed = 0
        idle_timeout_seconds = max(60, settings.policy.idle_timeout_minutes * 60)
        now = time.time()
        try:
            containers = docker_client.containers.list(
                all=True,
                filters={"name": f"{deployment.name_prefix}-"},
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
                        started_dt = datetime.fromisoformat(started_at)
                        idle_seconds = now - started_dt.timestamp()
                    except ValueError:
                        idle_seconds = 0
                if idle_seconds < idle_timeout_seconds:
                    continue
                try:
                    redis_client = _get_sync_redis_client(host)
                    last_active_raw = redis_client.get(f"sandbox:last_active:{container_name}")
                    if last_active_raw:
                        last_active = int(last_active_raw)
                        if now - last_active < idle_timeout_seconds:
                            continue
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning(
                        "Redis unavailable, skip idle cleanup for running sandbox %s: %s",
                        container_name,
                        exc,
                    )
                    continue
                with contextlib.suppress(Exception):
                    container.stop(timeout=10)
                try:
                    container.remove(force=True)
                    removed += 1
                    logger.info("Removed idle sandbox container: %s", container_name)
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("Failed to remove idle sandbox %s: %s", container_name, exc)
            return removed
        finally:
            pass

    @classmethod
    def _cleanup_broker_sandboxes_sync(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
    ) -> int:
        removed = 0
        idle_timeout_seconds = max(60, settings.policy.idle_timeout_minutes * 60)
        now = time.time()
        for item in _broker_list(host):
            name = item.get("id") or ""
            status = item.get("status") or ""
            if status in {"exited", "dead", "created"}:
                _broker_call(host, "DELETE", _broker_sandbox_path(name))
                removed += 1
                continue
            if status != "running":
                continue
            started_at = item.get("started_at") or ""
            try:
                started = datetime.fromisoformat(started_at).timestamp()
            except ValueError:
                continue
            if now - started < idle_timeout_seconds:
                continue
            try:
                last_active_raw = _get_sync_redis_client(host).get(f"sandbox:last_active:{name}")
                if last_active_raw and now - int(last_active_raw) < idle_timeout_seconds:
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
            _broker_call(host, "DELETE", _broker_sandbox_path(name))
            removed += 1
        return removed

    @classmethod
    async def cleanup_orphaned_containers(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
    ) -> int:
        return await asyncio.to_thread(
            cls._cleanup_orphaned_containers_sync,
            settings,
            host,
        )

    @classmethod
    async def get(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
        quota: SandboxQuota,
        sandbox_id: str,
    ) -> Self | None:
        """根据传递的id获取沙箱实例"""
        deployment = settings.deployment
        if deployment.address:
            try:
                ip = await cls._resolve_hostname_to_ip(deployment.address)
                return cls(
                    settings=settings,
                    host=host,
                    quota=quota,
                    ip=ip,
                    container_name=sandbox_id,
                )
            except (OSError, RuntimeError, ValueError) as e:
                logger.error("解析沙箱地址失败: %s", e)
                return None

        try:
            # 2.创建docker客户端并根据容器名字获取容器（在线程中执行同步 Docker SDK）
            ip = await asyncio.to_thread(
                cls._get_running_container_ip,
                settings,
                host,
                sandbox_id,
            )
            if not ip:
                return None
            return cls(
                settings=settings,
                host=host,
                quota=quota,
                ip=ip,
                container_name=sandbox_id,
            )
        except (OSError, RuntimeError, ValueError) as e:
            # 8.其他错误统一捕获
            logger.error("获取沙箱发生未知错误: %s", e)
            return None

    async def get_browser(
        self,
        llm: LLM | None = None,
        allowed_domains: frozenset[str] | None = None,
    ) -> Browser:
        """获取沙箱中的浏览器实例"""
        return PlaywrightBrowser(
            self.cdp_url,
            vision_enabled=bool(llm and llm.capabilities.vision),
            vision_llm=llm,
            allowed_domains=allowed_domains,
        )

    async def ensure_sandbox(self, max_retries: int | None = None) -> None:
        """确保沙箱一定存在/服务全部都开启了才执行后续步骤"""
        policy = self.settings.policy
        max_retries = max(1, max_retries or policy.warmup_max_retries)
        retry_interval = max(0.5, policy.warmup_retry_interval_seconds)

        # 2.循环请求获取supervisor状态并判断服务是否正常
        for _attempt in range(max_retries):
            try:
                # 3.调用client客户端向沙箱发起api请求获取状态
                response = await self.client.get(f"{self._base_url}/api/supervisor/status")
                response.raise_for_status()

                # 4.将响应结果转换为ToolResult
                tool_result = ToolResult.from_sandbox(**response.json())

                # 5.判断是否执行成功
                if not tool_result.success:
                    logger.warning("Supervisor进程状态监测失败: %s", tool_result.message)
                    await asyncio.sleep(retry_interval)
                    continue

                # 6.读取services数据并判断
                services = tool_result.data or []
                if not services:
                    logger.warning("Supervisor进程中未发现任何服务")
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
                    return
                logger.info(
                    "正在等待Sandbox Supervisor进程服务运行, 还未运行的服务列表: %s",
                    non_running_services,
                )
                await asyncio.sleep(retry_interval)
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
                logger.warning("无法确认Sandbox Supervisor进程状态: %s", e)
                await asyncio.sleep(retry_interval)

        # 经过max_retries次监测后还无法确认则抛出异常
        logger.error("在经过%s次尝试后仍无法确认Sandbox Supervisor状态信息", max_retries)
        raise DockerSandboxError(f"在经过{max_retries}次尝试后仍无法确认Sandbox Supervisor状态信息")

    async def read_file(
        self,
        filepath: str,
        start_line: int | None = None,
        end_line: int | None = None,
        sudo: bool = False,
        max_length: int = 10000,
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
            },
        )
        return ToolResult.from_sandbox(**response.json())

    async def read_files(
        self,
        filepaths: list[str],
        *,
        sudo: bool = False,
        max_length: int = 10000,
    ) -> list[ToolResult]:
        return await asyncio.gather(
            *(self.read_file(path, sudo=sudo, max_length=max_length) for path in filepaths)
        )

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
            },
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
            },
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
            },
        )
        return ToolResult.from_sandbox(**response.json())

    async def find_files(self, dir_path: str, glob_pattern: str) -> ToolResult:
        """查找沙箱中指定目录的文件列表"""
        response = await self.client.post(
            f"{self._base_url}/api/file/find-files",
            json={
                "dir_path": dir_path,
                "glob_pattern": glob_pattern,
            },
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
            },
        )
        return ToolResult.from_sandbox(**response.json())

    async def delete_file(self, filepath: str) -> ToolResult:
        """传递路径删除指定的文件"""
        response = await self.client.post(
            f"{self._base_url}/api/file/delete-file",
            json={
                "filepath": filepath,
            },
        )
        return ToolResult.from_sandbox(**response.json())

    async def upload_file(
        self,
        file_data: BinaryIO,
        filepath: str,
        filename: str | None = None,
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
            f"{self._base_url}/api/file/download-file", params={"filepath": filepath}
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
            },
        )
        return ToolResult.from_sandbox(**response.json())

    async def read_shell_output(self, session_id: str, console: bool = False) -> ToolResult:
        """读取沙箱中shell的输出"""
        response = await self.client.post(
            f"{self._base_url}/api/shell/read-shell-output",
            json={
                "session_id": session_id,
                "console": console,
            },
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
            },
        )
        return ToolResult.from_sandbox(**response.json())

    async def wait_process(self, session_id: str, seconds: int | None = None) -> ToolResult:
        """等待沙箱中进程的执行"""
        response = await self.client.post(
            f"{self._base_url}/api/shell/wait-process",
            json={
                "session_id": session_id,
                "seconds": seconds,
            },
        )
        return ToolResult.from_sandbox(**response.json())

    async def kill_process(self, session_id: str) -> ToolResult:
        """杀死沙箱中指定进程"""
        response = await self.client.post(
            f"{self._base_url}/api/shell/kill-process",
            json={
                "session_id": session_id,
            },
        )
        return ToolResult.from_sandbox(**response.json())

    _CHECKPOINT_SHELL_SESSION = "checkpoint"

    async def create_workspace_snapshot(self, snapshot_id: str) -> bytes:
        """Create a tar.gz snapshot of /home/ubuntu and return its bytes."""
        from app.domain.services.sandbox_snapshot_excludes import build_tar_exclude_args

        archive_path = f"/tmp/cp_{snapshot_id}.tgz"
        exclude_args = build_tar_exclude_args()
        create_cmd = f"tar czf {archive_path} -C /home/ubuntu {exclude_args} ."
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
            f"! -name '.snapshots' ! -name '.browser-profile' -exec rm -rf {{}} + && "
            f"tar xzf {archive_path} -C /home/ubuntu && rm -f {archive_path}"
        )
        result = await self.exec_command(
            self._CHECKPOINT_SHELL_SESSION,
            "/home/ubuntu",
            restore_cmd,
        )
        if not result.success:
            raise RuntimeError(f"恢复沙箱快照失败: {result.message or result.data}")

    _BROWSER_PROFILE_DIR = "/home/ubuntu/.browser-profile"

    async def stop_chrome(self) -> None:
        response = await self.client.post(f"{self._base_url}/api/supervisor/stop-chrome")
        response.raise_for_status()
        payload = response.json()
        tool_result = ToolResult.from_sandbox(**payload)
        if not tool_result.success:
            raise RuntimeError(f"停止浏览器失败: {tool_result.message or tool_result.data}")

    async def start_chrome(self) -> None:
        response = await self.client.post(f"{self._base_url}/api/supervisor/start-chrome")
        response.raise_for_status()
        payload = response.json()
        tool_result = ToolResult.from_sandbox(**payload)
        if not tool_result.success:
            raise RuntimeError(f"启动浏览器失败: {tool_result.message or tool_result.data}")

    async def restart_browser(self) -> None:
        response = await self.client.post(f"{self._base_url}/api/supervisor/restart-chrome")
        response.raise_for_status()
        payload = response.json()
        tool_result = ToolResult.from_sandbox(**payload)
        if not tool_result.success:
            raise RuntimeError(f"重启浏览器失败: {tool_result.message or tool_result.data}")

    async def create_browser_profile_snapshot(self, snapshot_id: str) -> bytes:
        """Live tar of .browser-profile without stopping Chrome (non-disruptive)."""
        archive_path = f"/tmp/bp_{snapshot_id}.tgz"
        create_cmd = f"tar czf {archive_path} -C /home/ubuntu .browser-profile"
        try:
            result = await self.exec_command(
                self._CHECKPOINT_SHELL_SESSION,
                "/home/ubuntu",
                create_cmd,
            )
            if not result.success:
                raise RuntimeError(f"创建浏览器快照失败: {result.message or result.data}")
            stream = await self.download_file(archive_path)
            return stream.read()
        finally:
            await self.exec_command(
                self._CHECKPOINT_SHELL_SESSION,
                "/home/ubuntu",
                f"rm -f {archive_path}",
            )

    async def restore_browser_profile_snapshot(
        self, snapshot_id: str, snapshot_data: BinaryIO
    ) -> None:
        archive_path = f"/tmp/bp_restore_{snapshot_id}.tgz"
        await self.stop_chrome()
        upload_result = await self.upload_file(
            file_data=snapshot_data,
            filepath=archive_path,
            filename=f"bp_restore_{snapshot_id}.tgz",
        )
        if not upload_result.success:
            raise RuntimeError(f"上传浏览器快照失败: {upload_result.message or upload_result.data}")

        restore_cmd = (
            f"rm -rf {self._BROWSER_PROFILE_DIR} && "
            f"tar xzf {archive_path} -C /home/ubuntu && rm -f {archive_path}"
        )
        result = await self.exec_command(
            self._CHECKPOINT_SHELL_SESSION,
            "/home/ubuntu",
            restore_cmd,
        )
        if not result.success:
            raise RuntimeError(f"恢复浏览器快照失败: {result.message or result.data}")
        await self.start_chrome()
