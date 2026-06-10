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


@dataclass(frozen=True)
class SandboxRuntimeSettings:
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

    @classmethod
    def from_config(cls, config: SandboxConfig) -> "SandboxRuntimeSettings":
        return cls(
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
        )


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
