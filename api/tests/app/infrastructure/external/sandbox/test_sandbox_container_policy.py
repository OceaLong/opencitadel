#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.infrastructure.external.runtime_settings import SandboxRuntimeSettings
from app.infrastructure.external.sandbox.sandbox_container_policy import (
    build_docker_sandbox_config,
)


def test_dynamic_sandbox_policy_is_non_root_ephemeral_and_resource_bounded():
    config = build_docker_sandbox_config(
        SandboxRuntimeSettings(
            image="sandbox:test",
            network="isolated",
            http_proxy="http://egress:3128",
            https_proxy="http://egress:3128",
            memory_limit="1g",
            cpu_limit=1.5,
            pids_limit=128,
        ),
        "opencitadel-sandbox-12345678",
    )

    assert config["user"] == "1000:1000"
    assert config["read_only"] is True
    assert config["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in config["security_opt"]
    assert config["pids_limit"] == 128
    assert config["mem_limit"] == "1g"
    assert config["network"] == "isolated"
    assert config["network"] != "opencitadel-network"
    assert config["environment"]["HTTP_PROXY"] == "http://egress:3128"
    assert config["environment"]["http_proxy"] == "http://egress:3128"
    assert "/home/ubuntu" in config["tmpfs"]
    assert "/tmp" in config["tmpfs"]
    assert config["init"] is True
