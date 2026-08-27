"""Immutable Sandbox deployment topology and one policy-bound decision."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.runtime_policy import SandboxOperationsPolicy
from app.infrastructure.external.sandbox.driver_resolve import (
    SandboxDriverName,
    resolve_sandbox_driver,
)
from core.config import DeploymentSettings


def _optional(value: str) -> str | None:
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class SandboxDeployment:
    """Restart-bound topology; contains no live resource policy or secret."""

    driver: SandboxDriverName
    address: str | None
    image: str | None
    name_prefix: str | None
    network: str | None
    chrome_args: str
    https_proxy: str | None
    http_proxy: str | None
    no_proxy: str | None
    k8s_namespace: str
    k8s_pod_label: str

    def __post_init__(self) -> None:
        if self.driver not in {"docker", "kubernetes"}:
            raise ValueError("sandbox driver must be docker or kubernetes")
        if not self.k8s_namespace:
            raise ValueError("sandbox Kubernetes namespace must not be empty")
        key, separator, value = self.k8s_pod_label.partition("=")
        if separator != "=" or not key.strip() or not value.strip():
            raise ValueError("sandbox Kubernetes pod label must use key=value")

    @classmethod
    def from_settings(cls, settings: DeploymentSettings) -> SandboxDeployment:
        return cls(
            driver=resolve_sandbox_driver(settings.sandbox_driver),
            address=_optional(settings.sandbox_address),
            image=_optional(settings.sandbox_image),
            name_prefix=_optional(settings.sandbox_name_prefix),
            network=_optional(settings.sandbox_network),
            chrome_args=settings.sandbox_chrome_args.strip(),
            https_proxy=_optional(settings.sandbox_https_proxy),
            http_proxy=_optional(settings.sandbox_http_proxy),
            no_proxy=_optional(settings.sandbox_no_proxy),
            k8s_namespace=settings.sandbox_k8s_namespace.strip(),
            k8s_pod_label=settings.sandbox_k8s_pod_label.strip(),
        )


@dataclass(frozen=True, slots=True)
class SandboxEffectiveSettings:
    """Exact deployment/policy pair used for one Sandbox decision."""

    deployment: SandboxDeployment
    operations_revision_id: UUID
    policy: SandboxOperationsPolicy


@dataclass(frozen=True, slots=True)
class SandboxHostAccess:
    """Injected process connectivity; kept separate from effective policy evidence."""

    environment: str
    broker_url: str | None
    broker_token: str | None
    redis_host: str
    redis_port: int
    redis_db: int
    redis_password: str | None

    @classmethod
    def from_settings(cls, settings: DeploymentSettings) -> SandboxHostAccess:
        return cls(
            environment=settings.env.strip().lower(),
            broker_url=_optional(settings.sandbox_broker_url),
            broker_token=_optional(settings.sandbox_broker_token),
            redis_host=settings.redis_host,
            redis_port=settings.redis_port,
            redis_db=settings.redis_db,
            redis_password=settings.redis_password,
        )


__all__ = ["SandboxDeployment", "SandboxEffectiveSettings", "SandboxHostAccess"]
