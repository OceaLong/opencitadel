#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.external.sandbox.kubernetes_sandbox import (
    KubernetesSandbox,
)


@pytest.mark.parametrize("sandbox_cls", [DockerSandbox, KubernetesSandbox])
def test_sandbox_exposes_batched_source_read_port(sandbox_cls):
    assert hasattr(sandbox_cls, "read_files")
