"""Closed, fail-closed resource envelope for untrusted Sandbox containers."""

from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.runtime_policy import SandboxOperationsPolicy
from app.infrastructure.external.sandbox.settings import SandboxDeployment


class SandboxContainerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ttl_minutes: int = Field(ge=1, le=10_080)
    memory_limit: str = Field(pattern=r"^[1-9][0-9]*[kKmMgG]$")
    cpu_limit: float = Field(ge=0.1, le=128)
    pids_limit: int = Field(ge=16, le=32_768)

    @classmethod
    def from_operations(cls, policy: SandboxOperationsPolicy) -> Self:
        return cls(
            ttl_minutes=policy.ttl_minutes,
            memory_limit=policy.memory_limit,
            cpu_limit=policy.cpu_limit,
            pids_limit=policy.pids_limit,
        )


class CreateSandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    operations_revision_id: UUID
    policy: SandboxContainerPolicy


def build_docker_sandbox_config(
    deployment: SandboxDeployment,
    policy: SandboxContainerPolicy,
    container_name: str,
    *,
    operations_revision_id: UUID,
) -> dict[str, Any]:
    chrome_args = deployment.chrome_args.strip()
    if "--no-sandbox" not in chrome_args.split():
        chrome_args = f"{chrome_args} --no-sandbox".strip()

    config: dict[str, Any] = {
        "image": deployment.image,
        "name": container_name,
        "detach": True,
        "remove": True,
        "init": True,
        "user": "1000:1000",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": {
            "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777",
            "/run": "rw,nosuid,nodev,noexec,size=32m,mode=0755",
            "/home/ubuntu": "rw,nosuid,nodev,size=768m,uid=1000,gid=1000,mode=0700",
        },
        "shm_size": "256m",
        "environment": {
            "SERVER_TIMEOUT_MINUTES": str(policy.ttl_minutes),
            "CHROME_ARGS": chrome_args,
            "HTTPS_PROXY": deployment.https_proxy or "",
            "HTTP_PROXY": deployment.http_proxy or "",
            "NO_PROXY": deployment.no_proxy or "",
            "https_proxy": deployment.https_proxy or "",
            "http_proxy": deployment.http_proxy or "",
            "no_proxy": deployment.no_proxy or "",
            "HOME": "/home/ubuntu",
        },
        "labels": {
            "opencitadel.io/sandbox": "true",
            "opencitadel.io/ephemeral": "true",
            "opencitadel.io/operations-revision": str(operations_revision_id),
        },
        "mem_limit": policy.memory_limit,
        "memswap_limit": policy.memory_limit,
        "nano_cpus": int(policy.cpu_limit * 1_000_000_000),
        "pids_limit": policy.pids_limit,
    }
    if deployment.network:
        config["network"] = deployment.network
    return config


__all__ = [
    "CreateSandboxRequest",
    "SandboxContainerPolicy",
    "build_docker_sandbox_config",
]
