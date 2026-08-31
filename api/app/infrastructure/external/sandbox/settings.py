"""Immutable Sandbox deployment topology and one policy-bound decision."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID

from app.domain.runtime_policy import SandboxOperationsPolicy
from app.infrastructure.external.sandbox.driver_resolve import (
    SandboxDriverName,
    resolve_sandbox_driver,
)
from core.config import DeploymentSettings

_DOCKER_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)?$")
_RESERVED_SANDBOX_LABELS = {
    "opencitadel.io/sandbox",
    "opencitadel.io/ephemeral",
    "opencitadel.io/operations-revision",
}
_ACCEPTANCE_OWNERSHIP_LABELS = {
    "com.docker.compose.project",
    "com.opencitadel.acceptance.project",
    "com.opencitadel.acceptance.run",
}


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
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.driver not in {"docker", "kubernetes"}:
            raise ValueError("sandbox driver must be docker or kubernetes")
        if not self.k8s_namespace:
            raise ValueError("sandbox Kubernetes namespace must not be empty")
        key, separator, value = self.k8s_pod_label.partition("=")
        if separator != "=" or not key.strip() or not value.strip():
            raise ValueError("sandbox Kubernetes pod label must use key=value")
        normalized: dict[str, str] = {}
        for label_key, label_value in self.labels.items():
            if not isinstance(label_key, str) or not _DOCKER_LABEL_PATTERN.fullmatch(label_key):
                raise ValueError("sandbox labels contain an invalid Docker label key")
            if label_key in _RESERVED_SANDBOX_LABELS:
                raise ValueError("sandbox labels cannot override runtime-managed labels")
            if not isinstance(label_value, str) or not label_value.strip():
                raise ValueError("sandbox labels must have non-empty string values")
            normalized[label_key] = label_value.strip()
        acceptance_keys = _ACCEPTANCE_OWNERSHIP_LABELS.intersection(normalized)
        if acceptance_keys and acceptance_keys != _ACCEPTANCE_OWNERSHIP_LABELS:
            raise ValueError("sandbox labels must provide the complete acceptance ownership set")
        object.__setattr__(self, "labels", MappingProxyType(dict(sorted(normalized.items()))))

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
            labels=settings.sandbox_labels,
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
