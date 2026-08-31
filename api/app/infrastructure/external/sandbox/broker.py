"""Narrow authenticated Docker lifecycle broker for ephemeral sandboxes."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import docker
from fastapi import Depends, FastAPI, Header, HTTPException

from app.infrastructure.external.sandbox.sandbox_container_policy import (
    CreateSandboxRequest,
    build_docker_sandbox_config,
)
from app.infrastructure.external.sandbox.settings import SandboxDeployment
from core.config import DeploymentSettings, load_deployment_settings

DockerClientFactory = Callable[[], Any]


@dataclass(frozen=True)
class BrokerRuntime:
    token: str
    deployment: SandboxDeployment
    docker_factory: DockerClientFactory


def _deployment(settings: DeploymentSettings) -> SandboxDeployment:
    deployment = SandboxDeployment.from_settings(settings)
    if not deployment.image:
        raise RuntimeError("SANDBOX_IMAGE must be configured")
    if not deployment.name_prefix:
        raise RuntimeError("SANDBOX_NAME_PREFIX must be configured")
    return deployment


def build_broker_runtime(
    settings: DeploymentSettings,
    *,
    docker_factory: DockerClientFactory = docker.from_env,
) -> BrokerRuntime:
    token = settings.sandbox_broker_token.strip()
    if len(token) < 32:
        raise RuntimeError("SANDBOX_BROKER_TOKEN must contain at least 32 characters")
    return BrokerRuntime(
        token=token,
        deployment=_deployment(settings),
        docker_factory=docker_factory,
    )


def _validate_name(name: str, runtime: BrokerRuntime) -> str:
    prefix = re.escape(runtime.deployment.name_prefix or "")
    if not re.fullmatch(rf"{prefix}-[a-f0-9]{{8}}", name):
        raise HTTPException(status_code=400, detail="invalid sandbox id")
    return name


async def _authorize(authorization: str, runtime: BrokerRuntime) -> None:
    expected = f"Bearer {runtime.token}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _container_payload(container, deployment: SandboxDeployment) -> dict:
    container.reload()
    networks = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
    preferred_network = deployment.network
    endpoint = networks.get(preferred_network) if preferred_network else None
    if endpoint is None and networks:
        endpoint = next(iter(networks.values()))
    return {
        "id": container.name.lstrip("/"),
        "status": container.status,
        "ip": (endpoint or {}).get("IPAddress") or "",
        "started_at": (container.attrs.get("State") or {}).get("StartedAt") or "",
    }


def _require_managed_sandbox(container, runtime: BrokerRuntime):
    container.reload()
    labels = (container.attrs.get("Config") or {}).get("Labels") or {}
    expected = {
        "opencitadel.io/sandbox": "true",
        **runtime.deployment.labels,
    }
    if any(labels.get(key) != value for key, value in expected.items()):
        raise HTTPException(status_code=404, detail="sandbox not found")
    return container


async def list_sandboxes(runtime: BrokerRuntime) -> dict:
    containers = runtime.docker_factory().containers.list(
        all=True,
        filters={
            "label": [
                "opencitadel.io/sandbox=true",
                *(f"{key}={value}" for key, value in runtime.deployment.labels.items()),
            ],
            "name": f"{runtime.deployment.name_prefix}-",
        },
    )
    return {"sandboxes": [_container_payload(item, runtime.deployment) for item in containers]}


async def create_sandbox(
    body: CreateSandboxRequest,
    runtime: BrokerRuntime,
) -> dict:
    sandbox_id = _validate_name(body.id, runtime)
    client = runtime.docker_factory()
    try:
        existing = client.containers.get(sandbox_id)
    except docker.errors.NotFound:
        existing = None
    if existing is not None:
        raise HTTPException(status_code=409, detail="sandbox already exists")
    container = client.containers.run(
        **build_docker_sandbox_config(
            runtime.deployment,
            body.policy,
            sandbox_id,
            operations_revision_id=body.operations_revision_id,
            access_token=body.access_token,
        )
    )
    payload = _container_payload(container, runtime.deployment)
    if not payload["ip"]:
        container.remove(force=True)
        raise HTTPException(status_code=503, detail="sandbox has no network address")
    return payload


async def get_sandbox(sandbox_id: str, runtime: BrokerRuntime) -> dict:
    _validate_name(sandbox_id, runtime)
    try:
        container = runtime.docker_factory().containers.get(sandbox_id)
    except docker.errors.NotFound as exc:
        raise HTTPException(status_code=404, detail="sandbox not found") from exc
    _require_managed_sandbox(container, runtime)
    return _container_payload(container, runtime.deployment)


async def delete_sandbox(sandbox_id: str, runtime: BrokerRuntime) -> dict:
    _validate_name(sandbox_id, runtime)
    try:
        container = runtime.docker_factory().containers.get(sandbox_id)
    except docker.errors.NotFound:
        return {"deleted": False}
    _require_managed_sandbox(container, runtime)
    container.remove(force=True)
    return {"deleted": True}


def create_broker_app(
    settings: DeploymentSettings | None = None,
    *,
    docker_factory: DockerClientFactory = docker.from_env,
) -> FastAPI:
    """Create one broker app with immutable validated process configuration."""

    runtime = build_broker_runtime(
        settings or load_deployment_settings(),
        docker_factory=docker_factory,
    )
    application = FastAPI(
        title="OpenCitadel Sandbox Broker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.runtime = runtime

    async def authorize(authorization: str = Header(default="")) -> None:
        await _authorize(authorization, runtime)

    @application.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @application.get("/v1/sandboxes", dependencies=[Depends(authorize)])
    async def list_route() -> dict:
        return await list_sandboxes(runtime)

    @application.post("/v1/sandboxes", dependencies=[Depends(authorize)])
    async def create_route(body: CreateSandboxRequest) -> dict:
        return await create_sandbox(body, runtime)

    @application.get(
        "/v1/sandboxes/{sandbox_id}",
        dependencies=[Depends(authorize)],
    )
    async def get_route(sandbox_id: str) -> dict:
        return await get_sandbox(sandbox_id, runtime)

    @application.delete(
        "/v1/sandboxes/{sandbox_id}",
        dependencies=[Depends(authorize)],
    )
    async def delete_route(sandbox_id: str) -> dict:
        return await delete_sandbox(sandbox_id, runtime)

    return application


def main() -> None:
    import uvicorn

    settings = load_deployment_settings()
    app = create_broker_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=8090, access_log=False)


if __name__ == "__main__":
    main()


__all__ = [
    "BrokerRuntime",
    "build_broker_runtime",
    "create_broker_app",
    "create_sandbox",
    "delete_sandbox",
    "get_sandbox",
    "list_sandboxes",
    "main",
]
