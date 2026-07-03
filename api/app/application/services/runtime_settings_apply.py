#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Push AppConfig sandbox/worker/streams into module-level runtime settings."""
from app.domain.models.app_config import AppConfig
from app.infrastructure.external.runtime_settings import (
    AdmissionRuntimeSettings,
    SandboxRuntimeSettings,
    TaskQueueRuntimeSettings,
    configure_admission_runtime,
)
from app.infrastructure.external.sandbox.docker_sandbox import configure_sandbox_runtime
from app.infrastructure.external.task.task_state import configure_task_state_runtime


def apply_runtime_settings(app_config: AppConfig) -> None:
    configure_sandbox_runtime(SandboxRuntimeSettings.from_config(app_config.sandbox))
    configure_admission_runtime(
        AdmissionRuntimeSettings.from_config(app_config.sandbox, app_config.worker),
    )
    configure_task_state_runtime(
        TaskQueueRuntimeSettings.from_config(app_config.streams, app_config.worker),
    )
