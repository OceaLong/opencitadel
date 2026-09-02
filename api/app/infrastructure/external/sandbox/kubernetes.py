"""Kubernetes lifecycle adapter for isolated per-Run sandboxes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from core.config import DeploymentSettings

CoreApiFactory = Callable[[], Any]


def _core_api() -> Any:
    from kubernetes import client, config

    config.load_incluster_config()
    return client.CoreV1Api()


def _memory_quantity(value: str) -> str:
    suffix = value[-1].lower()
    quantity = value[:-1]
    return f"{quantity}{'Gi' if suffix == 'g' else 'Mi' if suffix == 'm' else 'Ki'}"


def build_sandbox_pod(
    settings: DeploymentSettings,
    *,
    sandbox_id: str,
    access_token: str,
    ttl_minutes: int,
) -> dict[str, object]:
    label_key, separator, label_value = settings.sandbox_k8s_pod_label.partition("=")
    if not separator or not label_key.strip() or not label_value.strip():
        raise RuntimeError("SANDBOX_K8S_POD_LABEL must use key=value")
    if not settings.sandbox_image.strip():
        raise RuntimeError("SANDBOX_IMAGE is required")
    chrome_args = settings.sandbox_chrome_args.strip()
    if "--no-sandbox" not in chrome_args.split():
        chrome_args = f"{chrome_args} --no-sandbox".strip()
    memory = _memory_quantity("2g")
    environment = {
        "SERVER_TIMEOUT_MINUTES": str(ttl_minutes),
        "SANDBOX_ACCESS_TOKEN": access_token,
        "CHROME_ARGS": chrome_args,
        "HTTP_PROXY": settings.sandbox_http_proxy,
        "HTTPS_PROXY": settings.sandbox_https_proxy,
        "NO_PROXY": settings.sandbox_no_proxy,
        "http_proxy": settings.sandbox_http_proxy,
        "https_proxy": settings.sandbox_https_proxy,
        "no_proxy": settings.sandbox_no_proxy,
        "HOME": "/home/ubuntu",
    }
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": sandbox_id,
            "labels": {
                label_key.strip(): label_value.strip(),
                "opencitadel.io/sandbox": "true",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "activeDeadlineSeconds": ttl_minutes * 60,
            "terminationGracePeriodSeconds": 5,
            "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [
                {
                    "name": "sandbox",
                    "image": settings.sandbox_image.strip(),
                    "ports": [
                        {"name": "api", "containerPort": 8080},
                        {"name": "vnc", "containerPort": 5901},
                        {"name": "cdp", "containerPort": 9222},
                    ],
                    "env": [{"name": name, "value": value} for name, value in environment.items()],
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "256Mi"},
                        "limits": {"cpu": "2", "memory": memory},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                        "readOnlyRootFilesystem": True,
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                    },
                    "volumeMounts": [
                        {"name": "tmp", "mountPath": "/tmp"},
                        {"name": "run", "mountPath": "/run"},
                        {"name": "home", "mountPath": "/home/ubuntu"},
                    ],
                }
            ],
            "volumes": [
                {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}},
                {"name": "run", "emptyDir": {"sizeLimit": "32Mi"}},
                {"name": "home", "emptyDir": {"sizeLimit": "768Mi"}},
            ],
        },
    }


class KubernetesSandboxManager:
    def __init__(
        self,
        settings: DeploymentSettings,
        *,
        core_api_factory: CoreApiFactory = _core_api,
    ) -> None:
        self._settings = settings
        self._core_api_factory = core_api_factory
        self._api: Any | None = None
        self._created: set[str] = set()

    def _client(self) -> Any:
        if self._api is None:
            self._api = self._core_api_factory()
        return self._api

    async def endpoint(self, sandbox_id: str, access_token: str) -> str:
        namespace = self._settings.sandbox_k8s_namespace.strip()
        if not namespace:
            raise RuntimeError("SANDBOX_K8S_NAMESPACE is required")
        api = self._client()
        try:
            pod = await asyncio.to_thread(api.read_namespaced_pod, sandbox_id, namespace)
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise
            body = build_sandbox_pod(
                self._settings,
                sandbox_id=sandbox_id,
                access_token=access_token,
                ttl_minutes=60,
            )
            await asyncio.to_thread(api.create_namespaced_pod, namespace, body)
            self._created.add(sandbox_id)
            pod = None
        async with asyncio.timeout(30):
            while True:
                if pod is None:
                    pod = await asyncio.to_thread(api.read_namespaced_pod, sandbox_id, namespace)
                phase = str(getattr(pod.status, "phase", ""))
                ip = str(getattr(pod.status, "pod_ip", "") or "")
                if phase == "Running" and ip:
                    return ip
                if phase in {"Failed", "Succeeded"}:
                    raise RuntimeError(f"sandbox Pod entered terminal phase {phase}")
                pod = None
                await asyncio.sleep(0.25)

    async def close(self) -> None:
        if self._api is None or not self._created:
            return
        namespace = self._settings.sandbox_k8s_namespace.strip()
        for sandbox_id in sorted(self._created):
            try:
                await asyncio.to_thread(
                    self._api.delete_namespaced_pod,
                    sandbox_id,
                    namespace,
                    grace_period_seconds=0,
                )
            except Exception as exc:
                if getattr(exc, "status", None) != 404:
                    raise
        self._created.clear()


__all__ = ["KubernetesSandboxManager", "build_sandbox_pod"]
