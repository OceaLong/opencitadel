#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sandbox driver name resolution without circular imports."""
from __future__ import annotations

import os
from typing import Literal

SandboxDriverName = Literal["docker", "kubernetes"]


def resolve_sandbox_driver(configured: str = "auto") -> SandboxDriverName:
    explicit = (configured or "auto").strip().lower()
    if explicit in ("docker", "kubernetes"):
        return explicit  # type: ignore[return-value]
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    return "docker"
