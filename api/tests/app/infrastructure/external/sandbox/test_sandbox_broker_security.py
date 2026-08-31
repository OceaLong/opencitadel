from uuid import uuid4

import httpx
import pytest
from docker.errors import NotFound
from fastapi import HTTPException

from app.infrastructure.external.sandbox import docker_sandbox
from app.infrastructure.external.sandbox.broker import (
    _authorize,
    _require_managed_sandbox,
    _validate_name,
    build_broker_runtime,
    create_sandbox,
    list_sandboxes,
)
from app.infrastructure.external.sandbox.sandbox_container_policy import (
    CreateSandboxRequest,
    SandboxContainerPolicy,
)
from app.infrastructure.external.sandbox.settings import SandboxHostAccess
from core.config import DeploymentSettings


def _settings(
    *,
    token: str = "b" * 32,
    labels: dict[str, str] | None = None,
) -> DeploymentSettings:
    return DeploymentSettings(
        sandbox_broker_token=token,
        sandbox_driver="docker",
        sandbox_image="sandbox:test",
        sandbox_name_prefix="opencitadel-sandbox",
        sandbox_labels=labels or {},
    )


def test_broker_only_accepts_generated_sandbox_ids(monkeypatch):
    del monkeypatch
    runtime = build_broker_runtime(_settings())

    assert _validate_name("opencitadel-sandbox-a1b2c3d4", runtime) == (
        "opencitadel-sandbox-a1b2c3d4"
    )
    with pytest.raises(HTTPException):
        _validate_name("opencitadel-sandbox-../../host", runtime)


@pytest.mark.asyncio
async def test_broker_auth_uses_required_bearer_token(monkeypatch):
    del monkeypatch
    token = "b" * 32
    runtime = build_broker_runtime(_settings(token=token))

    await _authorize(f"Bearer {token}", runtime)
    with pytest.raises(HTTPException) as exc_info:
        await _authorize("Bearer wrong", runtime)

    assert exc_info.value.status_code == 401


def test_broker_client_encodes_sandbox_id_as_one_path_segment():
    assert (
        docker_sandbox._broker_sandbox_path("opencitadel-sandbox-a/b")
        == "/v1/sandboxes/opencitadel-sandbox-a%2Fb"
    )


def test_broker_client_converts_http_failures_to_runtime_errors(monkeypatch):
    request = httpx.Request("POST", "http://broker/v1/sandboxes")
    response = httpx.Response(
        400,
        request=request,
        json={"detail": "invalid sandbox id"},
    )

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, *args, **kwargs):
            return response

    monkeypatch.setattr(docker_sandbox.httpx, "Client", lambda **kwargs: _Client())
    host = SandboxHostAccess(
        environment="production",
        broker_url="http://broker",
        broker_token="b" * 32,
        redis_host="redis",
        redis_port=6379,
        redis_db=0,
        redis_password=None,
    )

    with pytest.raises(docker_sandbox.DockerSandboxError, match=r"HTTP 400.*invalid sandbox id"):
        docker_sandbox._broker_call(host, "POST", "/v1/sandboxes", json={})


def test_broker_rejects_same_prefix_container_without_sandbox_label():
    runtime = build_broker_runtime(_settings())
    container = type(
        "_Container",
        (),
        {
            "attrs": {"Config": {"Labels": {"other": "true"}}},
            "reload": lambda self: None,
        },
    )()

    with pytest.raises(HTTPException) as exc_info:
        _require_managed_sandbox(container, runtime)

    assert exc_info.value.status_code == 404


def test_broker_rejects_managed_container_from_another_acceptance_run():
    runtime = build_broker_runtime(
        _settings(
            labels={
                "com.docker.compose.project": "opencitadel-acceptance",
                "com.opencitadel.acceptance.project": "opencitadel-acceptance",
                "com.opencitadel.acceptance.run": "run-a",
            }
        )
    )
    container = type(
        "_Container",
        (),
        {
            "attrs": {
                "Config": {
                    "Labels": {
                        "opencitadel.io/sandbox": "true",
                        "com.docker.compose.project": "opencitadel-acceptance",
                        "com.opencitadel.acceptance.project": "opencitadel-acceptance",
                        "com.opencitadel.acceptance.run": "run-b",
                    }
                }
            },
            "reload": lambda self: None,
        },
    )()

    with pytest.raises(HTTPException) as exc_info:
        _require_managed_sandbox(container, runtime)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_broker_lists_only_sandboxes_with_every_runtime_label():
    class _Containers:
        def __init__(self):
            self.filters = None

        def list(self, *, all, filters):
            assert all is True
            self.filters = filters
            return []

    containers = _Containers()
    labels = {
        "com.docker.compose.project": "opencitadel-acceptance",
        "com.opencitadel.acceptance.project": "opencitadel-acceptance",
        "com.opencitadel.acceptance.run": "run-a",
    }
    runtime = build_broker_runtime(
        _settings(labels=labels),
        docker_factory=lambda: type("_Docker", (), {"containers": containers})(),
    )

    assert await list_sandboxes(runtime) == {"sandboxes": []}
    assert containers.filters == {
        "label": [
            "opencitadel.io/sandbox=true",
            "com.docker.compose.project=opencitadel-acceptance",
            "com.opencitadel.acceptance.project=opencitadel-acceptance",
            "com.opencitadel.acceptance.run=run-a",
        ],
        "name": "opencitadel-sandbox-",
    }


def test_production_never_falls_back_to_direct_docker():
    host = SandboxHostAccess(
        environment="production",
        broker_url=None,
        broker_token=None,
        redis_host="redis",
        redis_port=6379,
        redis_db=0,
        redis_password=None,
    )

    with pytest.raises(RuntimeError, match="direct Docker access is disabled"):
        docker_sandbox._get_docker_client(host)


@pytest.mark.asyncio
async def test_broker_create_uses_request_policy_not_resource_env(monkeypatch):
    revision_id = uuid4()

    class _Container:
        name = "opencitadel-sandbox-a1b2c3d4"
        status = "running"

        def __init__(self):
            self.attrs = {
                "NetworkSettings": {"Networks": {"isolated": {"IPAddress": "172.20.0.2"}}},
                "State": {"StartedAt": "2026-08-26T00:00:00+00:00"},
            }

        def reload(self):
            return None

    class _Containers:
        def __init__(self):
            self.run_kwargs = None

        def get(self, _sandbox_id):
            raise NotFound("missing")

        def run(self, **kwargs):
            self.run_kwargs = kwargs
            return _Container()

    containers = _Containers()
    runtime = build_broker_runtime(
        _settings(
            labels={
                "com.docker.compose.project": "opencitadel-acceptance",
                "com.opencitadel.acceptance.project": "opencitadel-acceptance",
                "com.opencitadel.acceptance.run": "run-a",
            }
        ),
        docker_factory=lambda: type("_Docker", (), {"containers": containers})(),
    )

    await create_sandbox(
        CreateSandboxRequest(
            id="opencitadel-sandbox-a1b2c3d4",
            operations_revision_id=revision_id,
            policy=SandboxContainerPolicy(
                ttl_minutes=17,
                memory_limit="512m",
                cpu_limit=1.25,
                pids_limit=64,
            ),
        ),
        runtime,
    )

    assert containers.run_kwargs["mem_limit"] == "512m"
    assert containers.run_kwargs["nano_cpus"] == 1_250_000_000
    assert containers.run_kwargs["environment"]["SERVER_TIMEOUT_MINUTES"] == "17"
    assert containers.run_kwargs["labels"]["opencitadel.io/operations-revision"] == str(revision_id)
    assert containers.run_kwargs["labels"]["com.opencitadel.acceptance.run"] == "run-a"
