#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sandbox driver resolution (docker vs kubernetes)."""
from __future__ import annotations

from typing import Type

from app.domain.external.sandbox import Sandbox
from app.infrastructure.external.runtime_settings import SandboxRuntimeSettings
from app.infrastructure.external.sandbox.driver_resolve import resolve_sandbox_driver

__all__ = ["resolve_sandbox_driver", "get_sandbox_class"]


def get_sandbox_class(settings: SandboxRuntimeSettings | None = None) -> Type[Sandbox]:
    from app.infrastructure.external.runtime_settings import get_sandbox_runtime_settings

    settings = settings or get_sandbox_runtime_settings()
    driver = resolve_sandbox_driver(settings.driver)
    if driver == "kubernetes":
        from app.infrastructure.external.sandbox.kubernetes_sandbox import KubernetesSandbox

        return KubernetesSandbox
    from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

    return DockerSandbox
