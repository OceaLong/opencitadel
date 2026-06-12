#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.app_config import SandboxConfig, WorkerConfig
from app.infrastructure.external.runtime_settings import AdmissionRuntimeSettings
from app.infrastructure.external.sandbox.node_id import resolve_node_id
from app.infrastructure.external.sandbox.driver_resolve import resolve_sandbox_driver


def test_resolve_sandbox_driver_explicit():
    assert resolve_sandbox_driver("docker") == "docker"
    assert resolve_sandbox_driver("kubernetes") == "kubernetes"


def test_admission_settings_from_config():
    sandbox = SandboxConfig(driver="auto")
    worker = WorkerConfig(max_sandboxes_per_node=3)
    settings = AdmissionRuntimeSettings.from_config(sandbox, worker)
    assert settings.max_sandboxes_per_node == 3
    assert settings.sandbox_driver in ("docker", "kubernetes")


def test_resolve_node_id_returns_string():
    assert resolve_node_id()
