from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.domain.runtime_policy import SandboxOperationsPolicy
from app.infrastructure.external.sandbox import docker_sandbox
from app.infrastructure.external.sandbox.docker_sandbox import (
    DockerSandbox,
    DockerSandboxError,
)
from app.infrastructure.external.sandbox.settings import (
    SandboxDeployment,
    SandboxEffectiveSettings,
    SandboxHostAccess,
)


def _settings() -> SandboxEffectiveSettings:
    return SandboxEffectiveSettings(
        deployment=SandboxDeployment(
            driver="docker",
            address=None,
            image="sandbox:test",
            name_prefix="opencitadel-sandbox",
            network="isolated",
            chrome_args="",
            https_proxy=None,
            http_proxy=None,
            no_proxy=None,
            k8s_namespace="default",
            k8s_pod_label="app=opencitadel-sandbox",
        ),
        operations_revision_id=uuid4(),
        policy=SandboxOperationsPolicy(admission_settle_seconds=0),
    )


def _host() -> SandboxHostAccess:
    return SandboxHostAccess(
        environment="production",
        broker_url="http://sandbox-broker:8090",
        broker_token="b" * 32,
        redis_host="redis",
        redis_port=6379,
        redis_db=0,
        redis_password=None,
    )


@pytest.mark.asyncio
async def test_failed_warmup_destroys_created_container(monkeypatch) -> None:
    quota = SimpleNamespace(
        acquire=AsyncMock(return_value=True),
        release=AsyncMock(),
    )
    sandbox = SimpleNamespace(
        ensure_sandbox=AsyncMock(side_effect=RuntimeError("warmup failed")),
        destroy=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        DockerSandbox,
        "_create_task_with_name",
        classmethod(lambda cls, *args: sandbox),
    )

    with pytest.raises(RuntimeError, match="warmup failed"):
        await DockerSandbox.create_and_warm(_settings(), _host(), quota)

    sandbox.destroy.assert_awaited_once_with()
    quota.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_warmup_returns_created_container(monkeypatch) -> None:
    quota = SimpleNamespace(
        acquire=AsyncMock(return_value=True),
        release=AsyncMock(),
    )
    sandbox = SimpleNamespace(
        id="opencitadel-sandbox-a1b2c3d4",
        ensure_sandbox=AsyncMock(),
        destroy=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        DockerSandbox,
        "_create_task_with_name",
        classmethod(lambda cls, *args: sandbox),
    )

    created = await DockerSandbox.create_and_warm(_settings(), _host(), quota)

    assert created is sandbox
    sandbox.ensure_sandbox.assert_awaited_once_with(max_retries=None)


@pytest.mark.asyncio
async def test_warmup_retries_transient_http_connection_failures(monkeypatch) -> None:
    quota = SimpleNamespace(release=AsyncMock())
    sandbox = DockerSandbox(
        settings=_settings(),
        host=_host(),
        quota=quota,
        ip="172.20.0.10",
        container_name="opencitadel-sandbox-a1b2c3d4",
    )
    request = httpx.Request("GET", "http://172.20.0.10:8080/api/supervisor/status")
    sandbox.client = SimpleNamespace(
        get=AsyncMock(side_effect=httpx.ConnectError("not ready", request=request))
    )
    sleep = AsyncMock()
    monkeypatch.setattr(docker_sandbox.asyncio, "sleep", sleep)

    with pytest.raises(DockerSandboxError, match="2次尝试"):
        await sandbox.ensure_sandbox(max_retries=2)

    assert sandbox.client.get.await_count == 2
    assert sleep.await_count == 2


def test_direct_create_removes_container_when_ip_resolution_fails(monkeypatch) -> None:
    container = SimpleNamespace(
        attrs={"NetworkSettings": {"Networks": {}}},
        reload=MagicMock(),
        remove=MagicMock(),
    )
    containers = SimpleNamespace(run=MagicMock(return_value=container))
    monkeypatch.setattr(
        docker_sandbox,
        "_get_docker_client",
        lambda host: SimpleNamespace(containers=containers),
    )
    host = SandboxHostAccess(
        environment="development",
        broker_url=None,
        broker_token=None,
        redis_host="redis",
        redis_port=6379,
        redis_db=0,
        redis_password=None,
    )

    with pytest.raises(DockerSandboxError, match="未分配到 IPv4"):
        DockerSandbox._create_task_with_name(
            _settings(),
            host,
            SimpleNamespace(),
            "opencitadel-sandbox-a1b2c3d4",
        )

    container.remove.assert_called_once_with(force=True)
