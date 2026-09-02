"""Immutable values exchanged by process composition roots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from app.application.ports.coordination import RedisConnectivity
from app.infrastructure.storage.cos import Cos
from app.infrastructure.storage.minio import Minio
from app.infrastructure.storage.postgres import Postgres
from app.infrastructure.storage.redis import RedisClient
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings

if TYPE_CHECKING:
    from app.composition.tasks import TaskSupervisor
    from app.contexts.identity.runtime import IdentityRuntime
    from app.contexts.inference.runtime import InferenceRuntime
    from app.contexts.kernel.runtime import KernelApiRuntime, KernelWorkerRuntime
    from app.contexts.knowledge.runtime import KnowledgeRuntime


class RuntimeReadiness:
    """Mutable lifecycle marker owned by an otherwise immutable runtime bundle."""

    def __init__(self) -> None:
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def mark_ready(self) -> None:
        self._ready = True

    def mark_not_ready(self) -> None:
        self._ready = False


@dataclass(frozen=True)
class ResourceBundle:
    """Process-owned infrastructure resources with one explicit lifetime."""

    settings: DeploymentSettings
    role: ProcessRole
    postgres: Postgres
    redis: RedisClient
    redis_connectivity: RedisConnectivity
    object_storage_client: Cos | Minio
    general_redis: Redis


@dataclass(frozen=True)
class ApiRuntime:
    """HTTP graph grouped by bounded context rather than service inventory."""

    settings: DeploymentSettings
    resources: ResourceBundle
    readiness: RuntimeReadiness
    supervisor: TaskSupervisor
    identity: IdentityRuntime
    inference: InferenceRuntime
    knowledge: KnowledgeRuntime
    kernel: KernelApiRuntime


@dataclass(frozen=True)
class KernelRuntime:
    """Worker graph grouped by the same bounded contexts."""

    settings: DeploymentSettings
    resources: ResourceBundle
    readiness: RuntimeReadiness
    supervisor: TaskSupervisor
    identity: IdentityRuntime
    inference: InferenceRuntime
    knowledge: KnowledgeRuntime
    kernel: KernelWorkerRuntime
