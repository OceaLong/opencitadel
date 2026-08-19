#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fail-closed runtime policy for untrusted Docker sandboxes."""
from typing import Any

from app.infrastructure.external.runtime_settings import SandboxRuntimeSettings


def build_docker_sandbox_config(
    settings: SandboxRuntimeSettings,
    container_name: str,
) -> dict[str, Any]:
    chrome_args = (settings.chrome_args or "").strip()
    if "--no-sandbox" not in chrome_args.split():
        chrome_args = f"{chrome_args} --no-sandbox".strip()

    config: dict[str, Any] = {
        "image": settings.image,
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
            "SERVER_TIMEOUT_MINUTES": str(settings.ttl_minutes or 60),
            "CHROME_ARGS": chrome_args,
            "HTTPS_PROXY": settings.https_proxy or "",
            "HTTP_PROXY": settings.http_proxy or "",
            "NO_PROXY": settings.no_proxy or "",
            "https_proxy": settings.https_proxy or "",
            "http_proxy": settings.http_proxy or "",
            "no_proxy": settings.no_proxy or "",
            "HOME": "/home/ubuntu",
        },
        "labels": {
            "opencitadel.io/sandbox": "true",
            "opencitadel.io/ephemeral": "true",
        },
    }
    if settings.memory_limit:
        config["mem_limit"] = settings.memory_limit
        config["memswap_limit"] = settings.memory_limit
    if settings.cpu_limit and settings.cpu_limit > 0:
        config["nano_cpus"] = int(settings.cpu_limit * 1_000_000_000)
    if settings.pids_limit and settings.pids_limit > 0:
        config["pids_limit"] = settings.pids_limit
    if settings.network:
        config["network"] = settings.network
    return config
