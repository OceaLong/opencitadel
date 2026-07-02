#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Runtime settings used by infrastructure adapters.

These small dataclasses keep infrastructure modules independent from the
application-level config provider while still allowing composition roots to
inject hot-loaded config values.
"""
from dataclasses import dataclass
from typing import Optional

from app.domain.models.app_config import SandboxConfig, StreamsConfig, WorkerConfig
from app.infrastructure.external.sandbox.driver_resolve import resolve_sandbox_driver


@dataclass(frozen=True)
class AdmissionRuntimeSettings:
    sandbox_driver: str = "docker"
    max_sandboxes_per_node: int = 4
    max_dynamic_sandboxes_global: int = 0
    admission_min_host_available_mb: int = 3072
    admission_reclaim_target_mb: int = 4096
    admission_poll_interval_seconds: float = 2.0
    admission_settle_seconds: float = 8.0
    admission_reclaim_enabled: bool = True
    task_execution_lease_seconds: int = 60
    reclaim_leader_lease_seconds: int = 15

    @classmethod
    def from_config(cls, sandbox: SandboxConfig, worker: WorkerConfig) -> "AdmissionRuntimeSettings":
        return cls(
            sandbox_driver=resolve_sandbox_driver(sandbox.driver),
            max_sandboxes_per_node=worker.max_sandboxes_per_node,
            max_dynamic_sandboxes_global=worker.max_dynamic_sandboxes_global,
            admission_min_host_available_mb=worker.admission_min_host_available_mb,
            admission_reclaim_target_mb=worker.admission_reclaim_target_mb,
            admission_poll_interval_seconds=worker.admission_poll_interval_seconds,
            admission_settle_seconds=worker.admission_settle_seconds,
            admission_reclaim_enabled=worker.admission_reclaim_enabled,
            task_execution_lease_seconds=worker.task_execution_lease_seconds,
            reclaim_leader_lease_seconds=worker.reclaim_leader_lease_seconds,
        )


_admission_runtime_settings = AdmissionRuntimeSettings()


def configure_admission_runtime(settings: AdmissionRuntimeSettings) -> None:
    global _admission_runtime_settings
    _admission_runtime_settings = settings
    from app.infrastructure.external.sandbox.admission import configure_admission_runtime as _configure_quota

    _configure_quota(settings)


def get_admission_runtime_settings() -> AdmissionRuntimeSettings:
    return _admission_runtime_settings


@dataclass(frozen=True)
class SandboxRuntimeSettings:
    driver: str = "auto"
    address: Optional[str] = None
    image: Optional[str] = None
    name_prefix: Optional[str] = None
    ttl_minutes: Optional[int] = 60
    network: Optional[str] = None
    chrome_args: Optional[str] = ""
    https_proxy: Optional[str] = None
    http_proxy: Optional[str] = None
    no_proxy: Optional[str] = None
    memory_limit: Optional[str] = "2g"
    cpu_limit: Optional[float] = 2.0
    pids_limit: Optional[int] = 512
    pool_enabled: bool = True
    pool_size: int = 2
    idle_timeout_minutes: int = 30
    warmup_retry_interval_seconds: float = 0.5
    warmup_max_retries: int = 30
    fast_warmup_max_retries: int = 5
    k8s_namespace: str = "default"
    k8s_pod_label: str = "app=opencitadel-sandbox"

    @classmethod
    def from_config(cls, config: SandboxConfig) -> "SandboxRuntimeSettings":
        return cls(
            driver=config.driver,
            address=config.address,
            image=config.image,
            name_prefix=config.name_prefix,
            ttl_minutes=config.ttl_minutes,
            network=config.network,
            chrome_args=config.chrome_args,
            https_proxy=config.https_proxy,
            http_proxy=config.http_proxy,
            no_proxy=config.no_proxy,
            memory_limit=config.memory_limit,
            cpu_limit=config.cpu_limit,
            pids_limit=config.pids_limit,
            pool_enabled=config.pool_enabled,
            pool_size=config.pool_size,
            idle_timeout_minutes=config.idle_timeout_minutes,
            warmup_retry_interval_seconds=config.warmup_retry_interval_seconds,
            k8s_namespace=config.k8s_namespace,
            k8s_pod_label=config.k8s_pod_label,
        )


_sandbox_runtime_settings = SandboxRuntimeSettings()


def configure_sandbox_runtime(settings: SandboxRuntimeSettings) -> None:
    global _sandbox_runtime_settings
    _sandbox_runtime_settings = settings


def get_sandbox_runtime_settings() -> SandboxRuntimeSettings:
    return _sandbox_runtime_settings


@dataclass(frozen=True)
class TaskQueueRuntimeSettings:
    dispatch_maxlen: int = 10000
    stream_maxlen: int = 10000
    task_dispatch_max_retries: int = 3

    @classmethod
    def from_config(
            cls,
            streams: StreamsConfig,
            worker: WorkerConfig,
    ) -> "TaskQueueRuntimeSettings":
        return cls(
            dispatch_maxlen=streams.dispatch_maxlen,
            stream_maxlen=streams.stream_maxlen,
            task_dispatch_max_retries=worker.task_dispatch_max_retries,
        )
