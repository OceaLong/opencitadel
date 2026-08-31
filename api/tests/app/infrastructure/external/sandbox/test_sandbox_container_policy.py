from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.runtime_policy import SandboxOperationsPolicy
from app.infrastructure.external.sandbox.sandbox_container_policy import (
    CreateSandboxRequest,
    SandboxContainerPolicy,
    build_docker_sandbox_config,
)
from app.infrastructure.external.sandbox.settings import SandboxDeployment


def _deployment(**updates) -> SandboxDeployment:
    values = {
        "driver": "docker",
        "address": None,
        "image": "sandbox:test",
        "name_prefix": "opencitadel-sandbox",
        "network": "isolated",
        "chrome_args": "",
        "https_proxy": "http://egress:3128",
        "http_proxy": "http://egress:3128",
        "no_proxy": None,
        "k8s_namespace": "default",
        "k8s_pod_label": "app=opencitadel-sandbox",
        "labels": {},
    }
    values.update(updates)
    return SandboxDeployment(**values)


def test_dynamic_sandbox_policy_is_non_root_ephemeral_and_resource_bounded():
    config = build_docker_sandbox_config(
        _deployment(),
        SandboxContainerPolicy(
            ttl_minutes=45,
            memory_limit="1g",
            cpu_limit=1.5,
            pids_limit=128,
        ),
        "opencitadel-sandbox-12345678",
        operations_revision_id=uuid4(),
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


def test_access_token_is_injected_into_container_env():
    # Regression: the sandbox data-plane HTTP API had no auth. The kernel now
    # injects a per-sandbox bearer token via env so the sandbox can reject
    # cross-container access; an empty token stays backward compatible.
    config = build_docker_sandbox_config(
        _deployment(),
        SandboxContainerPolicy(ttl_minutes=45, memory_limit="1g", cpu_limit=1.5, pids_limit=128),
        "opencitadel-sandbox-12345678",
        operations_revision_id=uuid4(),
        access_token="secret-token-xyz",
    )
    assert config["environment"]["SANDBOX_ACCESS_TOKEN"] == "secret-token-xyz"

    default_config = build_docker_sandbox_config(
        _deployment(),
        SandboxContainerPolicy(ttl_minutes=45, memory_limit="1g", cpu_limit=1.5, pids_limit=128),
        "opencitadel-sandbox-12345678",
        operations_revision_id=uuid4(),
    )
    assert default_config["environment"]["SANDBOX_ACCESS_TOKEN"] == ""


def test_create_sandbox_request_carries_access_token():
    request = CreateSandboxRequest(
        id="opencitadel-sandbox-12345678",
        operations_revision_id=uuid4(),
        policy=SandboxContainerPolicy(
            ttl_minutes=45, memory_limit="1g", cpu_limit=1.5, pids_limit=128
        ),
        access_token="broker-token-abc",
    )
    assert request.access_token == "broker-token-abc"


def test_hardened_dynamic_sandbox_disables_chromium_inner_sandbox():
    config = build_docker_sandbox_config(
        _deployment(chrome_args="--proxy-server=http://egress:3128"),
        SandboxContainerPolicy.from_operations(SandboxOperationsPolicy()),
        "opencitadel-sandbox-12345678",
        operations_revision_id=uuid4(),
    )

    assert config["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in config["security_opt"]
    assert config["environment"]["CHROME_ARGS"].split() == [
        "--proxy-server=http://egress:3128",
        "--no-sandbox",
    ]


def test_dynamic_sandbox_inherits_exact_deployment_ownership_labels():
    config = build_docker_sandbox_config(
        _deployment(
            labels={
                "com.docker.compose.project": "opencitadel-acceptance",
                "com.opencitadel.acceptance.project": "opencitadel-acceptance",
                "com.opencitadel.acceptance.run": "run-a",
            }
        ),
        SandboxContainerPolicy.from_operations(SandboxOperationsPolicy()),
        "opencitadel-sandbox-12345678",
        operations_revision_id=uuid4(),
    )

    assert config["labels"]["com.docker.compose.project"] == "opencitadel-acceptance"
    assert config["labels"]["com.opencitadel.acceptance.run"] == "run-a"
    assert config["labels"]["opencitadel.io/sandbox"] == "true"


@pytest.mark.parametrize(
    "labels",
    [
        {"not a label": "value"},
        {"com.opencitadel.acceptance.run": ""},
        {"com.opencitadel.acceptance.run": "run-a"},
        {"opencitadel.io/sandbox": "false"},
    ],
)
def test_sandbox_deployment_rejects_unsafe_ownership_labels(labels):
    with pytest.raises(ValueError, match="sandbox labels"):
        _deployment(labels=labels)


def test_broker_request_is_closed_and_carries_verified_policy_revision():
    revision_id = uuid4()
    request = CreateSandboxRequest(
        id="opencitadel-sandbox-12345678",
        operations_revision_id=revision_id,
        policy=SandboxContainerPolicy.from_operations(SandboxOperationsPolicy(memory_limit="512m")),
    )

    assert request.operations_revision_id == revision_id
    assert request.policy.memory_limit == "512m"
    with pytest.raises(ValidationError, match="legacy_memory_limit"):
        CreateSandboxRequest.model_validate(
            {
                **request.model_dump(mode="json"),
                "legacy_memory_limit": "8g",
            }
        )
