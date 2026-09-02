"""Small authenticated Docker control plane for per-Run sandboxes."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import docker
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.config import DeploymentSettings, load_deployment_settings

DockerClientFactory = Callable[[], Any]


class SandboxPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ttl_minutes: int = Field(ge=1, le=10_080)
    memory_limit: str = Field(pattern=r"^[1-9][0-9]*[kKmMgG]$")
    cpu_limit: float = Field(ge=0.1, le=128)
    pids_limit: int = Field(ge=16, le=32_768)


class CreateSandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    operations_revision_id: UUID
    policy: SandboxPolicy
    access_token: str = Field(min_length=1, max_length=512)


@dataclass(frozen=True, slots=True)
class BrokerRuntime:
    token: str
    image: str
    name_prefix: str
    network: str
    labels: dict[str, str]
    chrome_args: str
    http_proxy: str
    https_proxy: str
    no_proxy: str
    docker_factory: DockerClientFactory


def build_broker_runtime(
    settings: DeploymentSettings,
    *,
    docker_factory: DockerClientFactory = docker.from_env,
) -> BrokerRuntime:
    token = settings.sandbox_broker_token.strip()
    image = settings.sandbox_image.strip()
    prefix = settings.sandbox_name_prefix.strip()
    network = settings.sandbox_network.strip()
    if len(token) < 32:
        raise RuntimeError("SANDBOX_BROKER_TOKEN must contain at least 32 characters")
    if not image or not prefix or not network:
        raise RuntimeError("SANDBOX_IMAGE, SANDBOX_NAME_PREFIX and SANDBOX_NETWORK are required")
    return BrokerRuntime(
        token=token,
        image=image,
        name_prefix=prefix,
        network=network,
        labels=dict(settings.sandbox_labels),
        chrome_args=settings.sandbox_chrome_args.strip(),
        http_proxy=settings.sandbox_http_proxy.strip(),
        https_proxy=settings.sandbox_https_proxy.strip(),
        no_proxy=settings.sandbox_no_proxy.strip(),
        docker_factory=docker_factory,
    )


def _sandbox_id(value: str, runtime: BrokerRuntime) -> str:
    if not re.fullmatch(rf"{re.escape(runtime.name_prefix)}-[a-f0-9]{{8}}", value):
        raise HTTPException(status_code=400, detail="invalid sandbox id")
    return value


def _payload(container: Any, runtime: BrokerRuntime) -> dict[str, object]:
    container.reload()
    networks = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
    endpoint = networks.get(runtime.network) or {}
    return {
        "id": container.name.lstrip("/"),
        "status": container.status,
        "ip": endpoint.get("IPAddress") or "",
        "started_at": (container.attrs.get("State") or {}).get("StartedAt") or "",
    }


def _managed(container: Any, runtime: BrokerRuntime) -> Any:
    container.reload()
    labels = (container.attrs.get("Config") or {}).get("Labels") or {}
    required = {"opencitadel.io/sandbox": "true", **runtime.labels}
    if any(labels.get(key) != value for key, value in required.items()):
        raise HTTPException(status_code=404, detail="sandbox not found")
    return container


def _docker_config(body: CreateSandboxRequest, runtime: BrokerRuntime) -> dict[str, object]:
    chrome_args = runtime.chrome_args
    if "--no-sandbox" not in chrome_args.split():
        chrome_args = f"{chrome_args} --no-sandbox".strip()
    policy = body.policy
    return {
        "image": runtime.image,
        "name": body.id,
        "detach": True,
        "remove": True,
        "init": True,
        "user": "1000:1000",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "network": runtime.network,
        "tmpfs": {
            "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777",
            "/run": "rw,nosuid,nodev,noexec,size=32m,mode=0755",
            "/home/ubuntu": "rw,nosuid,nodev,size=768m,uid=1000,gid=1000,mode=0700",
        },
        "shm_size": "256m",
        "environment": {
            "SERVER_TIMEOUT_MINUTES": str(policy.ttl_minutes),
            "SANDBOX_ACCESS_TOKEN": body.access_token,
            "CHROME_ARGS": chrome_args,
            "HTTP_PROXY": runtime.http_proxy,
            "HTTPS_PROXY": runtime.https_proxy,
            "NO_PROXY": runtime.no_proxy,
            "http_proxy": runtime.http_proxy,
            "https_proxy": runtime.https_proxy,
            "no_proxy": runtime.no_proxy,
            "HOME": "/home/ubuntu",
        },
        "labels": {
            **runtime.labels,
            "opencitadel.io/sandbox": "true",
            "opencitadel.io/ephemeral": "true",
            "opencitadel.io/operations-revision": str(body.operations_revision_id),
        },
        "mem_limit": policy.memory_limit,
        "memswap_limit": policy.memory_limit,
        "nano_cpus": int(policy.cpu_limit * 1_000_000_000),
        "pids_limit": policy.pids_limit,
    }


def create_broker_app(
    settings: DeploymentSettings | None = None,
    *,
    docker_factory: DockerClientFactory = docker.from_env,
) -> FastAPI:
    runtime = build_broker_runtime(
        settings or load_deployment_settings(), docker_factory=docker_factory
    )
    application = FastAPI(
        title="OpenCitadel Sandbox Broker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    async def authorize(authorization: str = Header(default="")) -> None:
        if not hmac.compare_digest(authorization, f"Bearer {runtime.token}"):
            raise HTTPException(status_code=401, detail="unauthorized")

    @application.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @application.post("/v1/sandboxes", dependencies=[Depends(authorize)])
    async def create_sandbox(body: CreateSandboxRequest) -> dict[str, object]:
        _sandbox_id(body.id, runtime)
        client = runtime.docker_factory()
        try:
            client.containers.get(body.id)
        except docker.errors.NotFound:
            pass
        else:
            raise HTTPException(status_code=409, detail="sandbox already exists")
        container = client.containers.run(**_docker_config(body, runtime))
        payload = _payload(container, runtime)
        if not payload["ip"]:
            container.remove(force=True)
            raise HTTPException(status_code=503, detail="sandbox has no network address")
        return payload

    @application.get("/v1/sandboxes/{sandbox_id}", dependencies=[Depends(authorize)])
    async def get_sandbox(sandbox_id: str) -> dict[str, object]:
        _sandbox_id(sandbox_id, runtime)
        try:
            container = runtime.docker_factory().containers.get(sandbox_id)
        except docker.errors.NotFound as exc:
            raise HTTPException(status_code=404, detail="sandbox not found") from exc
        return _payload(_managed(container, runtime), runtime)

    @application.delete("/v1/sandboxes/{sandbox_id}", dependencies=[Depends(authorize)])
    async def delete_sandbox(sandbox_id: str) -> dict[str, bool]:
        _sandbox_id(sandbox_id, runtime)
        try:
            container = runtime.docker_factory().containers.get(sandbox_id)
        except docker.errors.NotFound:
            return {"deleted": False}
        _managed(container, runtime).remove(force=True)
        return {"deleted": True}

    return application


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_broker_app(load_deployment_settings()),
        host="0.0.0.0",
        port=8090,
        access_log=False,
    )


if __name__ == "__main__":
    main()
