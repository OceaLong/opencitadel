import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.domain.runtime_policy import SandboxOperationsPolicy
from app.infrastructure.external.sandbox import kubernetes_sandbox
from app.infrastructure.external.sandbox.kubernetes_sandbox import KubernetesSandbox
from app.infrastructure.external.sandbox.settings import (
    SandboxDeployment,
    SandboxEffectiveSettings,
    SandboxHostAccess,
)


def _settings() -> SandboxEffectiveSettings:
    return SandboxEffectiveSettings(
        deployment=SandboxDeployment(
            driver="kubernetes",
            address=None,
            image="sandbox:test",
            name_prefix="opencitadel-sandbox",
            network=None,
            chrome_args="",
            https_proxy=None,
            http_proxy=None,
            no_proxy=None,
            k8s_namespace="sandbox-system",
            k8s_pod_label="app=opencitadel-sandbox",
        ),
        operations_revision_id=uuid4(),
        policy=SandboxOperationsPolicy(admission_settle_seconds=0),
    )


def _host() -> SandboxHostAccess:
    return SandboxHostAccess(
        environment="production",
        broker_url=None,
        broker_token=None,
        redis_host="redis",
        redis_port=6379,
        redis_db=0,
        redis_password=None,
    )


@pytest.mark.asyncio
async def test_successful_warmup_returns_created_pod(monkeypatch) -> None:
    quota = SimpleNamespace(
        acquire=AsyncMock(return_value=True),
        release=AsyncMock(),
    )
    monkeypatch.setattr(
        KubernetesSandbox,
        "_create_pod_sync",
        classmethod(lambda cls, *args: "10.0.0.8"),
    )
    ensure = AsyncMock()
    monkeypatch.setattr(KubernetesSandbox, "ensure_sandbox", ensure)

    created = await KubernetesSandbox.create_and_warm(_settings(), _host(), quota)

    assert created.id.startswith("opencitadel-sandbox-")
    ensure.assert_awaited_once_with(max_retries=None)


@pytest.mark.asyncio
async def test_failed_warmup_destroys_created_pod(monkeypatch) -> None:
    quota = SimpleNamespace(
        acquire=AsyncMock(return_value=True),
        release=AsyncMock(),
    )
    monkeypatch.setattr(
        KubernetesSandbox,
        "_create_pod_sync",
        classmethod(lambda cls, *args: "10.0.0.8"),
    )
    monkeypatch.setattr(
        KubernetesSandbox,
        "ensure_sandbox",
        AsyncMock(side_effect=httpx.ConnectError("warmup failed")),
    )
    destroyed: list[str] = []

    async def destroy(sandbox: KubernetesSandbox) -> bool:
        destroyed.append(sandbox.id)
        return True

    monkeypatch.setattr(KubernetesSandbox, "destroy", destroy)

    with pytest.raises(httpx.ConnectError, match="warmup failed"):
        await KubernetesSandbox.create_and_warm(_settings(), _host(), quota)

    assert len(destroyed) == 1
    quota.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_warmup_retries_transient_http_connection_failures(monkeypatch) -> None:
    sandbox = KubernetesSandbox(
        settings=_settings(),
        host=_host(),
        quota=SimpleNamespace(release=AsyncMock()),
        ip="10.0.0.8",
        pod_name="opencitadel-sandbox-a1b2c3d4",
    )
    request = httpx.Request("GET", "http://10.0.0.8:8080/api/supervisor/status")
    sandbox.client = SimpleNamespace(
        get=AsyncMock(side_effect=httpx.ConnectError("not ready", request=request))
    )
    sleep = AsyncMock()
    monkeypatch.setattr(kubernetes_sandbox.asyncio, "sleep", sleep)

    with pytest.raises(RuntimeError, match="Supervisor 未就绪"):
        await sandbox.ensure_sandbox(max_retries=2)

    assert sandbox.client.get.await_count == 2
    assert sleep.await_count == 2


def test_create_timeout_deletes_the_created_pod(monkeypatch) -> None:
    class _Client:
        def __getattr__(self, name: str):
            del name
            return lambda **kwargs: SimpleNamespace(**kwargs)

    kubernetes = ModuleType("kubernetes")
    kubernetes.client = _Client()
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes)
    api = SimpleNamespace(
        create_namespaced_pod=MagicMock(),
        read_namespaced_pod=MagicMock(),
        delete_namespaced_pod=MagicMock(),
    )
    monkeypatch.setattr(KubernetesSandbox, "_api", classmethod(lambda cls: api))
    monkeypatch.setattr(
        kubernetes_sandbox.time,
        "time",
        MagicMock(side_effect=[0.0, 181.0]),
    )

    with pytest.raises(RuntimeError, match="Pod 启动超时"):
        KubernetesSandbox._create_pod_sync(
            _settings(),
            "opencitadel-sandbox-a1b2c3d4",
        )

    api.delete_namespaced_pod.assert_called_once_with(
        "opencitadel-sandbox-a1b2c3d4",
        "sandbox-system",
        grace_period_seconds=0,
    )
