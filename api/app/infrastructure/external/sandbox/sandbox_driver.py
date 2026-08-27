"""Explicit Sandbox driver resolution without runtime globals."""

from __future__ import annotations

from app.domain.external.sandbox import Sandbox
from app.infrastructure.external.sandbox.driver_resolve import resolve_sandbox_driver
from app.infrastructure.external.sandbox.settings import SandboxDeployment

__all__ = ["get_sandbox_class", "resolve_sandbox_driver"]


def get_sandbox_class(deployment: SandboxDeployment) -> type[Sandbox]:
    if deployment.driver == "kubernetes":
        from app.infrastructure.external.sandbox.kubernetes_sandbox import KubernetesSandbox

        return KubernetesSandbox
    from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

    return DockerSandbox
