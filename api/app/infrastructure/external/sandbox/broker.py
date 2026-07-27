#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Narrow authenticated Docker lifecycle broker for ephemeral sandboxes."""
from __future__ import annotations

import hmac
import os
import re

import docker
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.infrastructure.external.runtime_settings import SandboxRuntimeSettings
from app.infrastructure.external.sandbox.sandbox_container_policy import (
    build_docker_sandbox_config,
)

app = FastAPI(
    title="OpenCitadel Sandbox Broker",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def _broker_token() -> str:
    token = _required_env("SANDBOX_BROKER_TOKEN")
    if len(token) < 32:
        raise RuntimeError("SANDBOX_BROKER_TOKEN must contain at least 32 characters")
    return token


def _name_prefix() -> str:
    return (os.environ.get("SANDBOX_NAME_PREFIX") or "opencitadel-sandbox").strip()


def _validate_name(name: str) -> str:
    prefix = re.escape(_name_prefix())
    if not re.fullmatch(rf"{prefix}-[a-f0-9]{{8}}", name):
        raise HTTPException(status_code=400, detail="invalid sandbox id")
    return name


async def _authorize(authorization: str = Header(default="")) -> None:
    expected = f"Bearer {_broker_token()}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _runtime_settings() -> SandboxRuntimeSettings:
    return SandboxRuntimeSettings(
        driver="docker",
        image=_required_env("SANDBOX_IMAGE"),
        name_prefix=_name_prefix(),
        ttl_minutes=int(os.environ.get("SANDBOX_TTL_MINUTES") or "20"),
        network=(os.environ.get("SANDBOX_NETWORK") or "").strip() or None,
        chrome_args=(os.environ.get("SANDBOX_CHROME_ARGS") or "").strip(),
        https_proxy=(os.environ.get("SANDBOX_HTTPS_PROXY") or "").strip() or None,
        http_proxy=(os.environ.get("SANDBOX_HTTP_PROXY") or "").strip() or None,
        no_proxy=(os.environ.get("SANDBOX_NO_PROXY") or "").strip() or None,
        memory_limit=(os.environ.get("SANDBOX_MEMORY_LIMIT") or "1g").strip(),
        cpu_limit=float(os.environ.get("SANDBOX_CPU_LIMIT") or "2"),
        pids_limit=int(os.environ.get("SANDBOX_PIDS_LIMIT") or "512"),
        pool_enabled=False,
        pool_size=0,
    )


def _container_payload(container) -> dict:
    container.reload()
    networks = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
    preferred_network = _runtime_settings().network
    endpoint = networks.get(preferred_network) if preferred_network else None
    if endpoint is None and networks:
        endpoint = next(iter(networks.values()))
    return {
        "id": container.name.lstrip("/"),
        "status": container.status,
        "ip": (endpoint or {}).get("IPAddress") or "",
        "started_at": (container.attrs.get("State") or {}).get("StartedAt") or "",
    }


def _require_managed_sandbox(container):
    container.reload()
    labels = ((container.attrs.get("Config") or {}).get("Labels") or {})
    if labels.get("opencitadel.io/sandbox") != "true":
        # Do not reveal or operate on a same-prefix container outside the
        # broker's explicit ownership boundary.
        raise HTTPException(status_code=404, detail="sandbox not found")
    return container


class CreateSandboxRequest(BaseModel):
    id: str


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/v1/sandboxes", dependencies=[Depends(_authorize)])
async def list_sandboxes() -> dict:
    containers = docker.from_env().containers.list(
        all=True,
        filters={
            "label": "opencitadel.io/sandbox=true",
            "name": f"{_name_prefix()}-",
        },
    )
    return {"sandboxes": [_container_payload(item) for item in containers]}


@app.post("/v1/sandboxes", dependencies=[Depends(_authorize)])
async def create_sandbox(body: CreateSandboxRequest) -> dict:
    sandbox_id = _validate_name(body.id)
    client = docker.from_env()
    try:
        existing = client.containers.get(sandbox_id)
    except docker.errors.NotFound:
        existing = None
    if existing is not None:
        raise HTTPException(status_code=409, detail="sandbox already exists")
    container = client.containers.run(
        **build_docker_sandbox_config(_runtime_settings(), sandbox_id)
    )
    payload = _container_payload(container)
    if not payload["ip"]:
        container.remove(force=True)
        raise HTTPException(status_code=503, detail="sandbox has no network address")
    return payload


@app.get("/v1/sandboxes/{sandbox_id}", dependencies=[Depends(_authorize)])
async def get_sandbox(sandbox_id: str) -> dict:
    _validate_name(sandbox_id)
    try:
        container = docker.from_env().containers.get(sandbox_id)
    except docker.errors.NotFound as exc:
        raise HTTPException(status_code=404, detail="sandbox not found") from exc
    _require_managed_sandbox(container)
    return _container_payload(container)


@app.delete("/v1/sandboxes/{sandbox_id}", dependencies=[Depends(_authorize)])
async def delete_sandbox(sandbox_id: str) -> dict:
    _validate_name(sandbox_id)
    try:
        container = docker.from_env().containers.get(sandbox_id)
    except docker.errors.NotFound:
        return {"deleted": False}
    _require_managed_sandbox(container)
    container.remove(force=True)
    return {"deleted": True}


def main() -> None:
    import uvicorn

    _broker_token()
    _runtime_settings()
    uvicorn.run(app, host="0.0.0.0", port=8090, access_log=False)


if __name__ == "__main__":
    main()
