"""Owned deployment verification uses Docker-shaped documents, without Docker."""

from copy import deepcopy

import pytest


def inventory():
    binding = {
        "project": "capacity-owned",
        "invocation": "4e3878ce-b94c-44d5-9cbe-a52b01bb324e",
        "network_id": "a" * 64,
        "containers": {
            "b" * 64: {"service": "postgres", "role": "storage", "image": "sha256:" + "1" * 64},
            "c" * 64: {"service": "kernel", "role": "producer", "image": "sha256:" + "2" * 64},
            "d" * 64: {"service": "api", "role": "producer", "image": "sha256:" + "3" * 64},
        },
    }
    labels = {
        "com.docker.compose.project": binding["project"],
        "com.opencitadel.acceptance.project": binding["project"],
        "com.opencitadel.acceptance.run": binding["invocation"],
    }
    network = {
        "Id": binding["network_id"],
        "Internal": True,
        "Labels": labels,
        "Containers": {identity: {} for identity in binding["containers"]},
    }
    containers = {
        identity: {
            "Id": identity,
            "Image": item["image"],
            "Config": {"Labels": {**labels, "com.docker.compose.service": item["service"]}},
            "State": {"Running": identity != "d" * 64},
            "HostConfig": {"NetworkMode": binding["network_id"], "Privileged": False},
            "NetworkSettings": {
                "Ports": {},
                "Networks": {"owned": {"NetworkID": binding["network_id"]}},
            },
        }
        for identity, item in binding["containers"].items()
    }
    return binding, network, containers


def test_exact_network_membership_and_original_running_states():
    from scripts.execution_capacity.target import verify_deployment

    binding, network, containers = inventory()
    verified = verify_deployment(binding, network, containers)
    assert verified == {"c" * 64: True, "d" * 64: False}


@pytest.mark.parametrize(
    "mutation", ["foreign", "image", "network", "public", "role", "project", "privileged"]
)
def test_refuse_ambiguous_or_shared_deployment(mutation):
    from scripts.execution_capacity.target import verify_deployment

    binding, network, containers = deepcopy(inventory())
    kernel = containers["c" * 64]
    if mutation == "foreign":
        network["Containers"]["e" * 64] = {}
    elif mutation == "image":
        kernel["Image"] = "sha256:" + "9" * 64
    elif mutation == "network":
        kernel["NetworkSettings"]["Networks"]["other"] = {"NetworkID": "f" * 64}
    elif mutation == "public":
        kernel["NetworkSettings"]["Ports"] = {
            "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]
        }
    elif mutation == "role":
        binding["containers"]["c" * 64]["role"] = "ignored"
    elif mutation == "project":
        kernel["Config"]["Labels"]["com.docker.compose.project"] = "other"
    else:
        kernel["HostConfig"]["Privileged"] = True
    with pytest.raises(ValueError, match="deployment"):
        verify_deployment(binding, network, containers)


def test_live_endpoints_follow_stopped_and_started_exact_containers():
    from scripts.execution_capacity.target import verify_deployment

    binding, network, containers = inventory()
    network["Containers"].pop("d" * 64)
    assert verify_deployment(binding, network, containers, live_endpoints=True)["d" * 64] is False
    containers["c" * 64]["State"]["Running"] = False
    network["Containers"].pop("c" * 64)
    assert not any(verify_deployment(binding, network, containers, live_endpoints=True).values())
    containers["c" * 64]["State"]["Running"] = True
    with pytest.raises(ValueError, match="network"):
        verify_deployment(binding, network, containers, live_endpoints=True)
    network["Containers"]["c" * 64] = {}
    assert verify_deployment(binding, network, containers, live_endpoints=True)["c" * 64]


def test_child_created_without_live_endpoint_then_started_is_exactly_accounted():
    from scripts.execution_capacity.target import verify_deployment

    binding, network, containers = inventory()
    for identity in ("c" * 64, "d" * 64):
        containers[identity]["State"]["Running"] = False
        network["Containers"].pop(identity)
    identity = "e" * 64
    binding["containers"][identity] = {
        "service": "capacity-seed",
        "role": "producer",
        "image": "sha256:" + "2" * 64,
    }
    containers[identity] = deepcopy(containers["c" * 64])
    containers[identity]["Id"] = identity
    containers[identity]["Config"]["Labels"]["com.docker.compose.service"] = "capacity-seed"
    assert verify_deployment(binding, network, containers, live_endpoints=True)[identity] is False
    containers[identity]["State"]["Running"] = True
    network["Containers"][identity] = {}
    assert verify_deployment(binding, network, containers, live_endpoints=True)[identity] is True
    containers[identity]["Image"] = "sha256:" + "8" * 64
    with pytest.raises(ValueError, match="container"):
        verify_deployment(binding, network, containers, live_endpoints=True)


def test_broker_exemption_requires_fixed_module_and_exact_mounts():
    from scripts.execution_capacity.target import verify_deployment

    binding, network, containers = inventory()
    identity = "e" * 64
    binding["containers"][identity] = {
        "service": "opencitadel-sandbox-broker",
        "role": "capability-service",
        "image": "sha256:" + "4" * 64,
    }
    broker = deepcopy(containers["c" * 64])
    broker["Id"], broker["Image"] = identity, binding["containers"][identity]["image"]
    broker["Config"]["Labels"]["com.docker.compose.service"] = "opencitadel-sandbox-broker"
    broker["Config"].update(
        Entrypoint=None, Cmd=["python", "-m", "app.infrastructure.external.sandbox.broker"]
    )
    broker["Mounts"] = [
        {
            "Type": "bind",
            "Source": "/var/run/docker.sock",
            "Destination": "/var/run/docker.sock",
            "RW": True,
        },
        {
            "Type": "volume",
            "Source": "/owned/receipts",
            "Destination": "/var/lib/opencitadel-evaluation",
            "RW": True,
        },
    ]
    binding["broker"] = {
        "container_id": identity,
        "mounts": {
            "/var/run/docker.sock": ["bind", "/var/run/docker.sock", True],
            "/var/lib/opencitadel-evaluation": ["volume", "/owned/receipts", True],
        },
    }
    containers[identity] = broker
    network["Containers"][identity] = {}
    assert identity not in verify_deployment(binding, network, containers)
    broker["Config"]["Cmd"] = ["python", "-m", "foreign"]
    with pytest.raises(ValueError, match="broker"):
        verify_deployment(binding, network, containers)
