#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Kubernetes Pod-based dynamic sandbox driver."""
from __future__ import annotations

import asyncio
import io
import logging
import time
import uuid
from typing import BinaryIO, Optional, Self

import httpx

from app.domain.external.browser import Browser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser
from app.infrastructure.external.runtime_settings import get_sandbox_runtime_settings

logger = logging.getLogger(__name__)

_POD_LABEL_KEY = "app"
_POD_LABEL_VALUE = "opencitadel-sandbox"


def _parse_memory_limit(limit: str) -> str:
    value = (limit or "1g").strip().lower()
    if value.endswith("g"):
        return f"{int(float(value[:-1]))}Gi"
    if value.endswith("m"):
        return f"{int(float(value[:-1]))}Mi"
    return value


class KubernetesSandbox(Sandbox):
    def __init__(self, ip: Optional[str] = None, pod_name: Optional[str] = None) -> None:
        self.client = httpx.AsyncClient(timeout=600)
        self._ip = ip
        self._pod_name = pod_name
        self._base_url = f"http://{ip}:8080" if ip else ""
        self._vnc_url = f"ws://{ip}:5901" if ip else ""
        self._cdp_url = f"http://{ip}:9222" if ip else ""

    @property
    def id(self) -> str:
        return self._pod_name or "opencitadel-sandbox"

    @property
    def vnc_url(self) -> str:
        return self._vnc_url

    @property
    def cdp_url(self) -> str:
        return self._cdp_url

    @classmethod
    def _api(cls):
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        return client.CoreV1Api()

    @classmethod
    def _settings(cls):
        return get_sandbox_runtime_settings()

    @classmethod
    async def list_live_sandbox_ids(cls) -> set[str]:
        return await asyncio.to_thread(cls._list_live_sync)

    @classmethod
    def _list_live_sync(cls) -> set[str]:
        settings = cls._settings()
        api = cls._api()
        pods = api.list_namespaced_pod(
            namespace=settings.k8s_namespace,
            label_selector=f"{_POD_LABEL_KEY}={_POD_LABEL_VALUE}",
        )
        return {
            p.metadata.name
            for p in pods.items
            if p.status and p.status.phase == "Running" and p.metadata.name
        }

    @classmethod
    async def _create_and_warm(cls, max_retries: Optional[int] = None) -> Self:
        settings = cls._settings()
        if settings.address:
            return await cls._create_fresh()

        from app.infrastructure.external.sandbox.admission import get_sandbox_quota

        quota = get_sandbox_quota()
        pod_name = f"{settings.name_prefix}-{str(uuid.uuid4())[:8]}"
        if not await quota.acquire(pod_name):
            raise RuntimeError("沙箱准入未通过：集群配额不足")
        try:
            sandbox = await cls._create_fresh_with_name(pod_name)
            await sandbox.ensure_sandbox(max_retries=max_retries)
        except Exception:
            await quota.release(pod_name)
            raise
        from app.infrastructure.external.sandbox.sandbox_pool import SandboxPool

        await SandboxPool.touch_activity(sandbox.id)
        return sandbox

    @classmethod
    async def _create_fresh_with_name(cls, pod_name: str) -> Self:
        ip = await asyncio.to_thread(cls._create_pod_sync, pod_name)
        return cls(ip=ip, pod_name=pod_name)

    @classmethod
    def _create_pod_sync(cls, pod_name: str) -> str:
        settings = cls._settings()
        api = cls._api()
        from kubernetes import client

        mem = _parse_memory_limit(settings.memory_limit or "1g")
        cpu = str(settings.cpu_limit or 2)
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={
                    _POD_LABEL_KEY: _POD_LABEL_VALUE,
                    "opencitadel.io/sandbox": "true",
                },
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=settings.image or "opencitadel-sandbox",
                        ports=[
                            client.V1ContainerPort(container_port=8080),
                            client.V1ContainerPort(container_port=5901),
                            client.V1ContainerPort(container_port=9222),
                        ],
                        resources=client.V1ResourceRequirements(
                            requests={"memory": mem, "cpu": cpu},
                            limits={"memory": mem, "cpu": cpu},
                        ),
                        env=[
                            client.V1EnvVar(
                                name="SERVER_TIMEOUT_MINUTES",
                                value=str(settings.ttl_minutes or 60),
                            ),
                        ],
                    )
                ],
            ),
        )
        api.create_namespaced_pod(namespace=settings.k8s_namespace, body=pod)
        deadline = time.time() + 180
        while time.time() < deadline:
            p = api.read_namespaced_pod(pod_name, settings.k8s_namespace)
            if p.status and p.status.phase == "Running" and p.status.pod_ip:
                return p.status.pod_ip
            time.sleep(2)
        raise RuntimeError(f"沙箱 Pod 启动超时: {pod_name}")

    @classmethod
    async def create(cls) -> Self:
        settings = cls._settings()
        if settings.address:
            from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

            ip = await DockerSandbox._resolve_hostname_to_ip(settings.address)
            return cls(ip=ip)
        from app.infrastructure.external.sandbox.sandbox_pool import get_sandbox_pool

        pool = get_sandbox_pool()
        if pool.enabled:
            return await pool.acquire()
        return await cls._create_and_warm()

    @classmethod
    async def get(cls, id: str) -> Optional[Self]:
        settings = cls._settings()
        if settings.address:
            from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

            ip = await DockerSandbox._resolve_hostname_to_ip(settings.address)
            return cls(ip=ip, pod_name=id)
        ip = await asyncio.to_thread(cls._get_pod_ip_sync, id)
        if not ip:
            return None
        return cls(ip=ip, pod_name=id)

    @classmethod
    def _get_pod_ip_sync(cls, pod_name: str) -> Optional[str]:
        settings = cls._settings()
        api = cls._api()
        try:
            p = api.read_namespaced_pod(pod_name, settings.k8s_namespace)
            if p.status and p.status.phase == "Running" and p.status.pod_ip:
                return p.status.pod_ip
        except Exception:
            return None
        return None

    async def destroy(self) -> bool:
        holder_id = self._pod_name
        try:
            if self.client:
                await self.client.aclose()
            if self._pod_name:
                await asyncio.to_thread(self._delete_pod_sync, self._pod_name)
            if holder_id:
                from app.infrastructure.external.sandbox.admission import get_sandbox_quota

                await get_sandbox_quota().release(holder_id)
            return True
        except Exception as exc:
            logger.error("销毁 K8s 沙箱[%s]失败: %s", self._pod_name, exc)
            return False

    @classmethod
    def _delete_pod_sync(cls, pod_name: str) -> None:
        settings = cls._settings()
        api = cls._api()
        try:
            api.delete_namespaced_pod(
                pod_name,
                settings.k8s_namespace,
                grace_period_seconds=0,
            )
        except Exception:
            pass

    @classmethod
    async def cleanup_orphaned_containers(cls) -> int:
        return await asyncio.to_thread(cls._cleanup_sync)

    @classmethod
    def _cleanup_sync(cls) -> int:
        settings = cls._settings()
        api = cls._api()
        removed = 0
        idle_timeout_seconds = max(60, (settings.idle_timeout_minutes or 30) * 60)
        now = time.time()
        pods = api.list_namespaced_pod(
            namespace=settings.k8s_namespace,
            label_selector=f"{_POD_LABEL_KEY}={_POD_LABEL_VALUE}",
        )
        for pod in pods.items:
            name = pod.metadata.name
            if not name:
                continue
            phase = pod.status.phase if pod.status else ""
            if phase in {"Failed", "Succeeded"}:
                cls._delete_pod_sync(name)
                removed += 1
                continue
            if phase != "Running":
                continue
            try:
                import redis as sync_redis
                from core.config import get_settings

                cfg = get_settings()
                redis_client = sync_redis.Redis(
                    host=cfg.redis_host,
                    port=cfg.redis_port,
                    db=cfg.redis_db,
                    password=cfg.redis_password,
                    decode_responses=True,
                )
                last_active_raw = redis_client.get(f"sandbox:last_active:{name}")
                if last_active_raw and now - int(last_active_raw) < idle_timeout_seconds:
                    continue
            except Exception:
                continue
            cls._delete_pod_sync(name)
            removed += 1
        return removed

    async def get_browser(
            self,
            supports_multimodal: bool = False,
            llm: Optional[LLM] = None,
    ) -> Browser:
        return PlaywrightBrowser(
            self.cdp_url,
            supports_multimodal=supports_multimodal,
            vision_llm=llm,
        )

    async def ensure_sandbox(self, max_retries: Optional[int] = None) -> None:
        settings = self._settings()
        max_retries = max(1, max_retries or settings.warmup_max_retries)
        retry_interval = max(0.5, settings.warmup_retry_interval_seconds)
        for _ in range(max_retries):
            try:
                response = await self.client.get(f"{self._base_url}/api/supervisor/status")
                response.raise_for_status()
                tool_result = ToolResult.from_sandbox(**response.json())
                if tool_result.success and tool_result.data:
                    services = tool_result.data
                    if all(s.get("statename") == "RUNNING" for s in services):
                        from app.infrastructure.external.sandbox.sandbox_pool import SandboxPool

                        await SandboxPool.touch_activity(self.id)
                        return
            except Exception as exc:
                logger.warning("K8s sandbox warmup: %s", exc)
            await asyncio.sleep(retry_interval)
        raise RuntimeError("K8s 沙箱 Supervisor 未就绪")

    async def read_file(self, filepath: str, start_line: Optional[int] = None, end_line: Optional[int] = None,
                        sudo: bool = False, max_length: int = 10000) -> ToolResult:
        response = await self.client.post(
            f"{self._base_url}/api/file/read-file",
            json={"filepath": filepath, "start_line": start_line, "end_line": end_line, "sudo": sudo,
                  "max_length": max_length},
        )
        return ToolResult.from_sandbox(**response.json())

    async def write_file(self, filepath: str, content: str, append: bool = False,
                         leading_newline: bool = False, trailing_newline: bool = False,
                         sudo: bool = False) -> ToolResult:
        response = await self.client.post(
            f"{self._base_url}/api/file/write-file",
            json={"filepath": filepath, "content": content, "append": append,
                  "leading_newline": leading_newline, "trailing_newline": trailing_newline, "sudo": sudo},
        )
        return ToolResult.from_sandbox(**response.json())

    async def exec_command(self, session_id: str, exec_dir: str, command: str) -> ToolResult:
        response = await self.client.post(
            f"{self._base_url}/api/shell/exec-command",
            json={"session_id": session_id, "exec_dir": exec_dir, "command": command},
        )
        return ToolResult.from_sandbox(**response.json())

    async def download_file(self, filepath: str) -> BinaryIO:
        response = await self.client.get(
            f"{self._base_url}/api/file/download-file",
            params={"filepath": filepath},
        )
        response.raise_for_status()
        return io.BytesIO(response.content)

    async def upload_file(self, file_data: BinaryIO, filepath: str, filename: str = None) -> ToolResult:
        files = {"file": (filename or "upload", file_data, "application/octet-stream")}
        response = await self.client.post(
            f"{self._base_url}/api/file/upload-file",
            files=files,
            data={"filepath": filepath},
        )
        return ToolResult.from_sandbox(**response.json())

    async def create_workspace_snapshot(self, snapshot_id: str) -> bytes:
        from app.domain.services.sandbox_snapshot_excludes import build_tar_exclude_args

        archive_path = f"/tmp/cp_{snapshot_id}.tgz"
        exclude_args = build_tar_exclude_args()
        create_cmd = f"tar czf {archive_path} -C /home/ubuntu {exclude_args} ."
        result = await self.exec_command("checkpoint", "/home/ubuntu", create_cmd)
        if not result.success:
            raise RuntimeError(f"创建 K8s 沙箱工作区快照失败: {result.message or result.data}")
        try:
            stream = await self.download_file(archive_path)
            return stream.read()
        finally:
            await self.exec_command("checkpoint", "/home/ubuntu", f"rm -f {archive_path}")

    async def restore_workspace_snapshot(self, snapshot_id: str, snapshot_data: BinaryIO) -> None:
        archive_path = f"/tmp/cp_restore_{snapshot_id}.tgz"
        upload_result = await self.upload_file(
            file_data=snapshot_data,
            filepath=archive_path,
            filename=f"cp_restore_{snapshot_id}.tgz",
        )
        if not upload_result.success:
            raise RuntimeError(f"上传 K8s 沙箱快照失败: {upload_result.message or upload_result.data}")
        restore_cmd = (
            "find /home/ubuntu -mindepth 1 -maxdepth 1 "
            "! -name '.snapshots' ! -name '.browser-profile' -exec rm -rf {} + && "
            f"tar xzf {archive_path} -C /home/ubuntu && rm -f {archive_path}"
        )
        result = await self.exec_command("checkpoint", "/home/ubuntu", restore_cmd)
        if not result.success:
            raise RuntimeError(f"恢复 K8s 沙箱快照失败: {result.message or result.data}")

    async def create_browser_profile_snapshot(self, snapshot_id: str) -> bytes:
        archive_path = f"/tmp/bp_{snapshot_id}.tgz"
        create_cmd = f"tar czf {archive_path} -C /home/ubuntu .browser-profile"
        try:
            result = await self.exec_command("checkpoint", "/home/ubuntu", create_cmd)
            if not result.success:
                raise RuntimeError(f"创建 K8s 浏览器快照失败: {result.message or result.data}")
            stream = await self.download_file(archive_path)
            return stream.read()
        finally:
            await self.exec_command("checkpoint", "/home/ubuntu", f"rm -f {archive_path}")

    async def restore_browser_profile_snapshot(self, snapshot_id: str, snapshot_data: BinaryIO) -> None:
        archive_path = f"/tmp/bp_restore_{snapshot_id}.tgz"
        upload_result = await self.upload_file(
            file_data=snapshot_data,
            filepath=archive_path,
            filename=f"bp_restore_{snapshot_id}.tgz",
        )
        if not upload_result.success:
            raise RuntimeError(f"上传 K8s 浏览器快照失败: {upload_result.message or upload_result.data}")
        restore_cmd = (
            "rm -rf /home/ubuntu/.browser-profile && "
            f"tar xzf {archive_path} -C /home/ubuntu && rm -f {archive_path}"
        )
        result = await self.exec_command("checkpoint", "/home/ubuntu", restore_cmd)
        if not result.success:
            raise RuntimeError(f"恢复 K8s 浏览器快照失败: {result.message or result.data}")

    async def restart_browser(self) -> None:
        response = await self.client.post(f"{self._base_url}/api/supervisor/restart-chrome")
        response.raise_for_status()
        tool_result = ToolResult.from_sandbox(**response.json())
        if not tool_result.success:
            raise RuntimeError(f"重启 K8s 浏览器失败: {tool_result.message or tool_result.data}")

