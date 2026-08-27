"""Kubernetes Pod-based dynamic sandbox driver."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
import uuid
from typing import BinaryIO, Self

import httpx

from app.domain.external.browser import Browser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser
from app.infrastructure.external.sandbox.admission import SandboxQuota
from app.infrastructure.external.sandbox.settings import (
    SandboxEffectiveSettings,
    SandboxHostAccess,
)

logger = logging.getLogger(__name__)


def _parse_memory_limit(limit: str) -> str:
    value = (limit or "1g").strip().lower()
    if value.endswith("g"):
        return f"{int(float(value[:-1]))}Gi"
    if value.endswith("m"):
        return f"{int(float(value[:-1]))}Mi"
    return value


class KubernetesSandbox(Sandbox):
    def __init__(
        self,
        *,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
        quota: SandboxQuota,
        ip: str | None = None,
        pod_name: str | None = None,
    ) -> None:
        self.client = httpx.AsyncClient(timeout=600)
        self._ip = ip
        self._pod_name = pod_name
        self.settings = settings
        self._host = host
        self._quota = quota
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
        except (OSError, RuntimeError, ValueError):
            config.load_kube_config()
        return client.CoreV1Api()

    @classmethod
    async def list_live_sandbox_ids(
        cls,
        settings: SandboxEffectiveSettings,
    ) -> set[str]:
        return await asyncio.to_thread(cls._list_live_sync, settings)

    @classmethod
    def _list_live_sync(cls, settings: SandboxEffectiveSettings) -> set[str]:
        deployment = settings.deployment
        api = cls._api()
        pods = api.list_namespaced_pod(
            namespace=deployment.k8s_namespace,
            label_selector=deployment.k8s_pod_label,
        )
        return {
            p.metadata.name
            for p in pods.items
            if p.status and p.status.phase == "Running" and p.metadata.name
        }

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
            from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

            ip = await DockerSandbox._resolve_hostname_to_ip(deployment.address)
            return cls(settings=settings, host=host, quota=quota, ip=ip)
        if not deployment.name_prefix:
            raise RuntimeError("sandbox name prefix is not configured")
        pod_name = f"{deployment.name_prefix}-{str(uuid.uuid4())[:8]}"
        if not await quota.acquire(pod_name, settings.policy):
            raise RuntimeError("沙箱准入未通过：集群配额不足")
        sandbox: Self | None = None
        try:
            ip = await asyncio.to_thread(cls._create_pod_sync, settings, pod_name)
            sandbox = cls(
                settings=settings,
                host=host,
                quota=quota,
                ip=ip,
                pod_name=pod_name,
            )
            await sandbox.ensure_sandbox(max_retries=max_retries)
        except BaseException:
            try:
                if sandbox is None:
                    await quota.release(pod_name)
                else:
                    await asyncio.shield(sandbox.destroy())
            except Exception:
                logger.exception("Failed to compensate sandbox warmup failure: %s", pod_name)
            raise
        return sandbox

    @classmethod
    def _create_pod_sync(
        cls,
        settings: SandboxEffectiveSettings,
        pod_name: str,
    ) -> str:
        deployment = settings.deployment
        policy = settings.policy
        api = cls._api()
        from kubernetes import client

        mem = _parse_memory_limit(policy.memory_limit)
        cpu = str(policy.cpu_limit)
        configured_label_key, _, configured_label_value = deployment.k8s_pod_label.partition("=")
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={
                    configured_label_key: configured_label_value,
                    "opencitadel.io/sandbox": "true",
                    "app.kubernetes.io/component": "sandbox",
                    "opencitadel.io/operations-revision": str(settings.operations_revision_id),
                },
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                active_deadline_seconds=max(
                    60,
                    policy.ttl_minutes * 60,
                ),
                automount_service_account_token=False,
                enable_service_links=False,
                security_context=client.V1PodSecurityContext(
                    run_as_non_root=True,
                    run_as_user=1000,
                    run_as_group=1000,
                    fs_group=1000,
                    seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                ),
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=deployment.image or "opencitadel-sandbox",
                        ports=[
                            client.V1ContainerPort(container_port=8080),
                            client.V1ContainerPort(container_port=5901),
                            client.V1ContainerPort(container_port=9222),
                        ],
                        resources=client.V1ResourceRequirements(
                            requests={"memory": mem, "cpu": cpu},
                            limits={"memory": mem, "cpu": cpu},
                        ),
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            privileged=False,
                            read_only_root_filesystem=True,
                            run_as_non_root=True,
                            run_as_user=1000,
                            run_as_group=1000,
                            capabilities=client.V1Capabilities(drop=["ALL"]),
                            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                        ),
                        volume_mounts=[
                            client.V1VolumeMount(
                                name="workspace",
                                mount_path="/home/ubuntu",
                            ),
                            client.V1VolumeMount(
                                name="tmp",
                                mount_path="/tmp",
                            ),
                            client.V1VolumeMount(
                                name="run",
                                mount_path="/run",
                            ),
                        ],
                        env=[
                            client.V1EnvVar(
                                name="SERVER_TIMEOUT_MINUTES",
                                value=str(policy.ttl_minutes),
                            ),
                        ],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="workspace",
                        empty_dir=client.V1EmptyDirVolumeSource(
                            size_limit=mem,
                        ),
                    ),
                    client.V1Volume(
                        name="tmp",
                        empty_dir=client.V1EmptyDirVolumeSource(
                            medium="Memory",
                            size_limit="256Mi",
                        ),
                    ),
                    client.V1Volume(
                        name="run",
                        empty_dir=client.V1EmptyDirVolumeSource(
                            medium="Memory",
                            size_limit="32Mi",
                        ),
                    ),
                ],
            ),
        )
        api.create_namespaced_pod(namespace=deployment.k8s_namespace, body=pod)
        try:
            deadline = time.time() + 180
            while time.time() < deadline:
                p = api.read_namespaced_pod(pod_name, deployment.k8s_namespace)
                if p.status and p.status.phase == "Running" and p.status.pod_ip:
                    return p.status.pod_ip
                time.sleep(2)
            raise RuntimeError(f"沙箱 Pod 启动超时: {pod_name}")
        except BaseException:
            with contextlib.suppress(Exception):
                api.delete_namespaced_pod(
                    pod_name,
                    deployment.k8s_namespace,
                    grace_period_seconds=0,
                )
            raise

    @classmethod
    async def get(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
        quota: SandboxQuota,
        sandbox_id: str,
    ) -> Self | None:
        deployment = settings.deployment
        if deployment.address:
            from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

            ip = await DockerSandbox._resolve_hostname_to_ip(deployment.address)
            return cls(
                settings=settings,
                host=host,
                quota=quota,
                ip=ip,
                pod_name=sandbox_id,
            )
        ip = await asyncio.to_thread(cls._get_pod_ip_sync, settings, sandbox_id)
        if not ip:
            return None
        return cls(
            settings=settings,
            host=host,
            quota=quota,
            ip=ip,
            pod_name=sandbox_id,
        )

    @classmethod
    def _get_pod_ip_sync(
        cls,
        settings: SandboxEffectiveSettings,
        pod_name: str,
    ) -> str | None:
        api = cls._api()
        try:
            p = api.read_namespaced_pod(
                pod_name,
                settings.deployment.k8s_namespace,
            )
            if p.status and p.status.phase == "Running" and p.status.pod_ip:
                return p.status.pod_ip
        except (OSError, RuntimeError, ValueError):
            return None
        return None

    async def destroy(self) -> bool:
        holder_id = self._pod_name
        destroyed = True
        try:
            if self.client:
                await self.client.aclose()
            if self._pod_name:
                await asyncio.to_thread(
                    self._delete_pod_sync,
                    self.settings,
                    self._pod_name,
                )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("销毁 K8s 沙箱[%s]失败: %s", self._pod_name, exc)
            destroyed = False
        finally:
            if holder_id:
                await self._quota.release(holder_id)
        return destroyed

    @classmethod
    def _delete_pod_sync(
        cls,
        settings: SandboxEffectiveSettings,
        pod_name: str,
    ) -> None:
        api = cls._api()
        with contextlib.suppress(Exception):
            api.delete_namespaced_pod(
                pod_name,
                settings.deployment.k8s_namespace,
                grace_period_seconds=0,
            )

    @classmethod
    async def cleanup_orphaned_containers(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
    ) -> int:
        return await asyncio.to_thread(cls._cleanup_sync, settings, host)

    @classmethod
    def _cleanup_sync(
        cls,
        settings: SandboxEffectiveSettings,
        host: SandboxHostAccess,
    ) -> int:
        deployment = settings.deployment
        api = cls._api()
        removed = 0
        idle_timeout_seconds = max(60, settings.policy.idle_timeout_minutes * 60)
        now = time.time()
        pods = api.list_namespaced_pod(
            namespace=deployment.k8s_namespace,
            label_selector=deployment.k8s_pod_label,
        )
        for pod in pods.items:
            name = pod.metadata.name
            if not name:
                continue
            phase = pod.status.phase if pod.status else ""
            if phase in {"Failed", "Succeeded"}:
                cls._delete_pod_sync(settings, name)
                removed += 1
                continue
            if phase != "Running":
                continue
            try:
                import redis as sync_redis

                redis_client = sync_redis.Redis(
                    host=host.redis_host,
                    port=host.redis_port,
                    db=host.redis_db,
                    password=host.redis_password,
                    decode_responses=True,
                )
                last_active_raw = redis_client.get(f"sandbox:last_active:{name}")
                if last_active_raw and now - int(last_active_raw) < idle_timeout_seconds:
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
            cls._delete_pod_sync(settings, name)
            removed += 1
        return removed

    async def get_browser(
        self,
        llm: LLM | None = None,
        allowed_domains: frozenset[str] | None = None,
    ) -> Browser:
        return PlaywrightBrowser(
            self.cdp_url,
            vision_enabled=bool(llm and llm.capabilities.vision),
            vision_llm=llm,
            allowed_domains=allowed_domains,
        )

    async def ensure_sandbox(self, max_retries: int | None = None) -> None:
        policy = self.settings.policy
        max_retries = max(1, max_retries or policy.warmup_max_retries)
        retry_interval = max(0.5, policy.warmup_retry_interval_seconds)
        for _ in range(max_retries):
            try:
                response = await self.client.get(f"{self._base_url}/api/supervisor/status")
                response.raise_for_status()
                tool_result = ToolResult.from_sandbox(**response.json())
                if tool_result.success and tool_result.data:
                    services = tool_result.data
                    if all(s.get("statename") == "RUNNING" for s in services):
                        return
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                logger.warning("K8s sandbox warmup: %s", exc)
            await asyncio.sleep(retry_interval)
        raise RuntimeError("K8s 沙箱 Supervisor 未就绪")

    async def read_file(
        self,
        filepath: str,
        start_line: int | None = None,
        end_line: int | None = None,
        sudo: bool = False,
        max_length: int = 10000,
    ) -> ToolResult:
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

    async def upload_file(
        self, file_data: BinaryIO, filepath: str, filename: str | None = None
    ) -> ToolResult:
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
            raise RuntimeError(
                f"上传 K8s 沙箱快照失败: {upload_result.message or upload_result.data}"
            )
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

    async def restore_browser_profile_snapshot(
        self, snapshot_id: str, snapshot_data: BinaryIO
    ) -> None:
        archive_path = f"/tmp/bp_restore_{snapshot_id}.tgz"
        upload_result = await self.upload_file(
            file_data=snapshot_data,
            filepath=archive_path,
            filename=f"bp_restore_{snapshot_id}.tgz",
        )
        if not upload_result.success:
            raise RuntimeError(
                f"上传 K8s 浏览器快照失败: {upload_result.message or upload_result.data}"
            )
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
