#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest
from fastapi import HTTPException

from app.infrastructure.external.sandbox.broker import (
    _authorize,
    _require_managed_sandbox,
    _validate_name,
)
from app.infrastructure.external.sandbox import docker_sandbox


def test_broker_only_accepts_generated_sandbox_ids(monkeypatch):
    monkeypatch.setenv("SANDBOX_NAME_PREFIX", "opencitadel-sandbox")

    assert (
        _validate_name("opencitadel-sandbox-a1b2c3d4")
        == "opencitadel-sandbox-a1b2c3d4"
    )
    with pytest.raises(HTTPException):
        _validate_name("opencitadel-sandbox-../../host")


@pytest.mark.asyncio
async def test_broker_auth_uses_required_bearer_token(monkeypatch):
    token = "b" * 32
    monkeypatch.setenv("SANDBOX_BROKER_TOKEN", token)

    await _authorize(f"Bearer {token}")
    with pytest.raises(HTTPException) as exc_info:
        await _authorize("Bearer wrong")

    assert exc_info.value.status_code == 401


def test_broker_client_encodes_sandbox_id_as_one_path_segment():
    assert (
        docker_sandbox._broker_sandbox_path("opencitadel-sandbox-a/b")
        == "/v1/sandboxes/opencitadel-sandbox-a%2Fb"
    )


def test_broker_rejects_same_prefix_container_without_sandbox_label():
    container = type(
        "_Container",
        (),
        {
            "attrs": {"Config": {"Labels": {"other": "true"}}},
            "reload": lambda self: None,
        },
    )()

    with pytest.raises(HTTPException) as exc_info:
        _require_managed_sandbox(container)

    assert exc_info.value.status_code == 404


def test_production_never_falls_back_to_direct_docker(monkeypatch):
    monkeypatch.setattr(
        docker_sandbox,
        "get_settings",
        lambda: type("Settings", (), {"env": "production"})(),
    )
    docker_sandbox._docker_client = None

    with pytest.raises(RuntimeError, match="direct Docker access is disabled"):
        docker_sandbox._get_docker_client()
